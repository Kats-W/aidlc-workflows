# Component Dependency — au Jibun Bank AI Agent

本ドキュメントはコンポーネント間・ユニット間の依存関係、DynamoDB テーブル定義、S3 バケット構造、および Lambda → DynamoDB / Bedrock / EventBridge のアクセスパターンを定義する。

---

## 1. ユニット間依存グラフ

```
U-01 Core Infrastructure（KMS / Logs / Secrets / Storage 基盤）
  │  （全ユニットが基盤に依存）
  ├──────────────┬──────────────┬──────────────┬──────────────┐
  ▼              ▼              ▼              ▼              ▼
U-02           U-03 ──────────► U-04          U-05          U-06
Knowledge      AI Conversation  Omnichannel   SDK &         Self-Improvement
Pipeline       Engine           & Escalation  Profile       Pipeline
  │              ▲   ▲             ▲             ▲             ▲
  │ VectorStore  │   │ uses        │ uses        │ profile     │ ContactAnalysis
  └──────────────┘   │ profile     │ session     │ resolve     │ ← Contact Lens/CSAT
                     └─────────────┴─────────────┘             │
                                                               ▼
                                                          U-07 Admin Dashboard
                                                          （ImprovementSuggestions /
                                                            メトリクス閲覧・承認）
```

**依存順序（ビルド・デプロイ）**: `U-01 → U-02 → U-03 → U-04 → U-05 → U-06 → U-07`

- U-02 は U-03（RAG）が参照する VectorStore を生成（U-03 は U-02 の出力に依存）。
- U-04・U-05 は U-03 の会話文脈・顧客解決に依存。
- U-06 は U-03（履歴・CSAT）と Contact Lens 出力に依存。
- U-07 は U-06（提案）と全ユニットのメトリクスに依存。

---

## 2. コンポーネント依存マトリクス

凡例: ✓ = 直接依存（呼び出し / 利用）

| ↓依存元 \ 依存先→ | BedrockClient | PiiMasker | VectorStore | CosineSearcher | HistoryRepo | S3ContentStore | SessionCtxMgr | Secrets |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| CrawlerLambda | | | | | | ✓ | | |
| EmbedderLambda | ✓ | | ✓ | | | ✓ | | |
| RagHandlerLambda | ✓ | ✓ | ✓ | ✓ | ✓ | | | |
| PersonalizerLambda | | | | | ✓ | | | |
| CsatHandlerLambda | | | | | ✓ | | | |
| ChannelSwitchLambda | | | | | ✓ | | ✓ | |
| EscalationLambda | | | | | | | ✓ | |
| CustomerProfileLambda | | | | | | | | |
| CrmWriterLambda | | | | | ✓ | | | ✓ |
| ContactLensAnalyzerLambda | | | | | | | | |
| GapAnalyzerLambda | ✓ | | | | | | | |
| SuggestionGeneratorLambda | ✓ | | | | | | | |
| DashboardApiLambda | | | | | | | | |
| MetricsAggregatorLambda | | | | | ✓ | | | |

> Internal Module（BedrockClient / PiiMasker / VectorStore / CosineSimilaritySearcher / HistoryRepository / S3ContentStore / SessionContextManager）は各 Lambda にバンドルされ、外部サービス（Bedrock, Comprehend, DynamoDB, S3, Secrets Manager）へのアクセスを抽象化する。

---

## 3. DynamoDB テーブル定義

すべてのテーブルは KMS（U-01 CMK）による保存時暗号化、PITR 有効、オンデマンドキャパシティを基本とする。

### 3.1 VectorStore テーブル

| 項目 | 定義 |
|---|---|
| 用途 | ナレッジチャンクの埋め込みベクトル格納（RAG 検索の対象） |
| PK | `chunkId` (S) |
| SK | （単一キー、SK なし） |
| 主要属性 | `embedding` (L<N> 1024 次元 / 数値リスト), `text` (S), `sourceUrl` (S), `contentHash` (S), `lang` (S), `crawledAt` (S, ISO8601) |
| TTL | なし（差分管理で明示削除） |
| GSI | `gsi_sourceUrl`（PK=`sourceUrl`）: URL 単位の削除・更新用 |
| アクセス | EmbedderLambda が upsert/delete、RagHandlerLambda（CosineSimilaritySearcher）が全件 scan |

### 3.2 CustomerHistory テーブル

| 項目 | 定義 |
|---|---|
| 用途 | 顧客会話履歴・ターン・サマリー・CSAT・セッション文脈 |
| PK | `customerId` (S) |
| SK | `sk` (S) — `TURN#<timestamp>` / `SUMMARY#<contactId>` / `CSAT#<contactId>` / `SESSION#<contactId>` |
| 主要属性 | `role` (S), `text` (S, PII マスク済み), `timestamp` (S), `contactId` (S), `channel` (S), `csatScore` (N), `summary` (S) |
| TTL | `expiresAt` (N, epoch) — 90 日 |
| GSI | `gsi_contactId`（PK=`contactId`, SK=`sk`）: チャネル切替・コンタクト単位参照用 |
| アクセス | RagHandler/Personalizer が query（直近 5 件）、CsatHandler が put、ChannelSwitch が query、CrmWriter が read |

### 3.3 ImprovementSuggestions テーブル

| 項目 | 定義 |
|---|---|
| 用途 | 週次生成の改善提案と承認ステータス |
| PK | `suggestionId` (S) |
| SK | （単一キー） |
| 主要属性 | `weekStart` (S), `targetUrl` (S), `suggestion` (S), `priorityScore` (N), `status` (S: `pending`/`approved`/`rejected`/`held`), `createdAt` (S), `evidenceContactIds` (L) |
| TTL | なし（運用判断のため永続） |
| GSI | `gsi_status`（PK=`status`, SK=`priorityScore`）: ダッシュボードの絞り込み・優先度順表示用 / `gsi_week`（PK=`weekStart`）: 週次集計用 |
| アクセス | SuggestionGenerator が put、DashboardApi が query/update |

### 3.4 ContentDiff テーブル

| 項目 | 定義 |
|---|---|
| 用途 | 前回クロールのチャンクハッシュ状態（差分検出の基準） |
| PK | `chunkId` (S) |
| SK | （単一キー） |
| 主要属性 | `contentHash` (S), `sourceUrl` (S), `s3Key` (S), `lastSeenAt` (S, ISO8601) |
| TTL | なし（最新状態を保持。削除分は明示削除） |
| GSI | `gsi_sourceUrl`（PK=`sourceUrl`）: URL 単位の差分判定用 |
| アクセス | CrawlerLambda（DifferEngine）が query/put/delete |

### 3.5 ContactAnalysis テーブル

| 項目 | 定義 |
|---|---|
| 用途 | 低品質コンタクト抽出結果・ギャップ分析中間データ |
| PK | `weekStart` (S) |
| SK | `contactId` (S) |
| 主要属性 | `csatScore` (N), `escalated` (BOOL), `transcriptSummary` (S, PII マスク済み), `gapCategory` (S), `confusionScore` (N), `analyzedAt` (S) |
| TTL | `expiresAt` (N, epoch) — 180 日 |
| GSI | `gsi_gapCategory`（PK=`gapCategory`, SK=`confusionScore`）: ギャップ集約・順位付け用 |
| アクセス | ContactLensAnalyzer が put、GapAnalyzer が query/update、SuggestionGenerator が query |

---

## 4. S3 バケット構造

### 4.1 ContentBucket（クローリングコンテンツ）

```
s3://<env>-jibun-ai-content/
  ├── crawled/
  │     └── <yyyy>/<mm>/<dd>/<sourceUrlHash>/<chunkId>.txt   # クロール本文（チャンク単位）
  ├── diff/
  │     └── <yyyy-mm-dd>/diff-result.json                    # DiffResult（Embedder への受け渡し）
  └── raw/
        └── <yyyy-mm-dd>/<sourceUrlHash>.html                # 生 HTML（監査・再パース用）
```

- 暗号化: SSE-KMS（U-01 CMK）。バージョニング有効。パブリックアクセス全ブロック。
- ライフサイクル: `raw/` は 30 日で削除、`diff/` は 90 日で削除。

### 4.2 DashboardHostingBucket（U-07）

```
s3://<env>-jibun-ai-dashboard/   # Amplify ビルド成果物（React 静的ホスティング）
```

---

## 5. Lambda → DynamoDB アクセスパターン

| Lambda | テーブル | 操作 | アクセスパターン |
|---|---|---|---|
| EmbedderLambda | VectorStore | PutItem / DeleteItem | chunkId 単位 upsert / delete |
| RagHandlerLambda | VectorStore | Scan | 全件スキャン（CosineSimilaritySearcher, /tmp 15 分キャッシュ） |
| RagHandlerLambda | CustomerHistory | Query / PutItem | `customerId` + `begins_with(sk, "TURN#")` 直近 5 件取得 / ターン追記 |
| PersonalizerLambda | CustomerHistory | Query | `customerId` 直近 5 件サマリー取得 |
| CsatHandlerLambda | CustomerHistory | PutItem | `sk="CSAT#<contactId>"` 記録 |
| ChannelSwitchLambda | CustomerHistory | Query（gsi_contactId） | `contactId` でターン取得・要約 |
| CrmWriterLambda | CustomerHistory | Query | `customerId` のサマリー取得 |
| CrawlerLambda | ContentDiff | Query / PutItem / DeleteItem | chunkId / gsi_sourceUrl で差分判定・状態更新 |
| ContactLensAnalyzerLambda | ContactAnalysis | PutItem | `weekStart`+`contactId` で低品質コンタクト記録 |
| GapAnalyzerLambda | ContactAnalysis | Query / UpdateItem | `weekStart` 取得 / gapCategory・confusionScore 更新 |
| SuggestionGeneratorLambda | ContactAnalysis | Query（gsi_gapCategory） | カテゴリ・スコア順取得 |
| SuggestionGeneratorLambda | ImprovementSuggestions | PutItem | 提案保存（≤ 10 件） |
| DashboardApiLambda | ImprovementSuggestions | Query（gsi_status）/ UpdateItem | ステータス絞り込み一覧 / ステータス更新 |
| MetricsAggregatorLambda | CustomerHistory | Query / Scan | 週次コンタクト・チャネル・ターン数集計 |

> IAM は各 Lambda ロールにリソース ARN 指定で最小権限を付与（読み取りのみ / 特定テーブルのみ等）。GSI ARN も明示。

---

## 6. Lambda → Bedrock アクセスパターン

| Lambda | モデル | API | 用途 |
|---|---|---|---|
| EmbedderLambda | `amazon.titan-embed-text-v2:0` | InvokeModel | チャンク埋め込み（1024 次元） |
| RagHandlerLambda | `amazon.titan-embed-text-v2:0` | InvokeModel | クエリ埋め込み |
| RagHandlerLambda | `anthropic.claude-sonnet-4-6-20250514-v1:0` | InvokeModel | RAG 回答生成 |
| GapAnalyzerLambda | `anthropic.claude-sonnet-4-6-20250514-v1:0` | InvokeModel | ナレッジギャップ分析（JSON 出力） |
| SuggestionGeneratorLambda | `anthropic.claude-sonnet-4-6-20250514-v1:0` | InvokeModel | 改善提案文生成 |

> Bedrock IAM はモデル ARN を resource 指定。スロットリングに対し指数バックオフ（最大 3 回）。

### Lambda → その他 AWS サービス

| Lambda | サービス | 用途 |
|---|---|---|
| RagHandlerLambda / ContactLensAnalyzerLambda | Amazon Comprehend | PII 検出・マスク |
| CrmWriterLambda | Secrets Manager | CRM API クレデンシャル取得 |
| ContactLensAnalyzerLambda | Amazon Connect / Contact Lens | 会話品質分析結果取得 |
| CustomerProfileLambda | Amazon Connect Customer Profiles | 顧客属性解決（au ID ハッシュ） |

---

## 7. EventBridge スケジューラー → Lambda トリガー一覧

| スケジュール名 | Cron（JST） | ターゲット Lambda | 内容 |
|---|---|---|---|
| `weekly-crawl-schedule` | 日曜 02:00 | `CrawlerLambda` | 週次クローリング → 差分検出 → S3 保存 |
| `weekly-improvement-schedule` | 日曜 04:00 | `ContactLensAnalyzerLambda` | 週次低品質コンタクト分析の起点 |
| `weekly-metrics-schedule` | 月曜 06:00 | `MetricsAggregatorLambda` | 週次利用統計の事前集計 |

### EventBridge カスタムイベント（パイプライン連鎖）

| イベント | 発行元 | 購読先 | 受け渡し |
|---|---|---|---|
| `knowledge.diff.ready` | CrawlerLambda | EmbedderLambda | DiffResult（S3 `diff/` 参照） |
| `improvement.analysis.ready` | ContactLensAnalyzerLambda | GapAnalyzerLambda | `weekStart` |
| `improvement.gaps.ready` | GapAnalyzerLambda | SuggestionGeneratorLambda | `weekStart` |

> 非同期 Lambda 呼び出しは自動リトライ（最大 2 回）+ SQS DLQ。詳細は `services.md` のエラーハンドリング戦略を参照。

---

## 8. CDK スタック間依存

```
CoreInfraStack（KMS, Logs, Secrets）
  ▼ exports: kmsKeyArn, logGroupArns, secretArns
StorageStack（DynamoDB ×5, S3 ×2）
  ▼ exports: tableArns/names, bucketNames
KnowledgeStack ─┐
ConnectStack ───┼─► AgentStack ─► ImprovementStack ─► DashboardStack
                │   （RAG/会話/SDK Lambda）  （改善 Lambda）   （API/Cognito/Amplify）
```

- 下位スタックは CloudFormation Export / SSM Parameter / CDK クロススタック参照で上位の出力を消費。
- `StorageStack` のテーブル名・ARN は環境変数（`VECTOR_TABLE_NAME`, `HISTORY_TABLE_NAME`, `SUGGESTIONS_TABLE_NAME`, `CONTENT_BUCKET_NAME` 等）として各 Lambda に注入。
