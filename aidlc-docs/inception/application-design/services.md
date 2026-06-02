# Services — au Jibun Bank AI Agent

本ドキュメントはユニットを横断するサービスレイヤー（オーケストレーション層）を定義する。各サービスは複数の Lambda / Internal Module を協調させ、1 つのビジネスユースケースを完結させる論理的単位である。サービス間の通信パターン（同期 / 非同期）とエラーハンドリング戦略（リトライ・DLQ・アラーム）を明記する。

---

## 1. サービス一覧

| サービス | 対象ユニット | トリガー | 通信パターン | レイテンシ制約 |
|---|---|---|---|---|
| `ConversationService` | U-03 / U-04 / U-05 | Connect コンタクトフロー（同期 Lambda） | 同期 | Connect 内 8 秒以内 |
| `KnowledgeService` | U-02 | EventBridge Scheduler（週次・日曜深夜） | 非同期 | 数分〜数十分（バッチ） |
| `ImprovementService` | U-06 | EventBridge Scheduler（週次） | 非同期 | 数分（バッチ） |
| `SessionService` | U-04 | Connect チャネル切替イベント（同期 Lambda） | 同期 | 8 秒以内 |
| `DashboardService` | U-07 | API Gateway（Cognito 認証） | 同期 | 通常 API（〜3 秒） |

---

## 2. ConversationService（会話オーケストレーション）

**目的**: Connect → Lex → RAG → 履歴保存までの 1 ターンを 8 秒以内に完結させる。

### オーケストレーションフロー

```
Connect コンタクトフロー
  │  (Lex v2: ASR/NLU/Polly Neural Kazuha)
  ▼
[RagHandlerLambda]  ← 同期呼び出し（Connect Lambda 統合）
  ├─ 1. CustomerProfileLambda 解決済み customer_id を event 属性から取得
  ├─ 2. PiiMasker.mask(user_input)                 # Comprehend
  ├─ 3. PersonalizerLambda.build_context(cid)      # 直近 5 件サマリー（並行取得可）
  ├─ 4. BedrockClient.embed(masked_input)          # Titan v2
  ├─ 5. CosineSimilaritySearcher.search(vec, k=5)  # /tmp キャッシュ 15 分
  ├─ 6. BedrockClient.generate_answer(prompt)      # Claude claude-sonnet-4-6
  ├─ 7. 出典 URL 付与・hit 判定
  ├─ 8. HistoryRepository.append_turn(...)         # 非同期 fire-and-forget 可（TTL 90 日）
  └─ 9. hit == False → EscalationLambda へ誘導フラグ返却
  ▼
Connect 属性へ {answer, sources, escalate} をマップ
  │  (コンタクト終了時)
  ├─ CsatHandlerLambda（CSAT 記録）
  └─ CrmWriterLambda（CRM へサマリー書き込み・非同期）
```

### 8 秒予算配分（目安）

| ステップ | 予算 |
|---|---|
| PII マスク（Comprehend） | ~0.5s |
| 履歴取得（DynamoDB） | ~0.3s |
| 埋め込み（Titan） | ~0.7s |
| ベクトル検索（キャッシュヒット時） | ~0.3s（ミス時 +1〜2s スキャン） |
| 回答生成（Claude） | ~4s |
| 履歴保存 | 非同期化（応答後） |
| バッファ | 残余。`TimeoutBudgetExceeded` 時はフォールバック定型応答 + エスカレーション |

### 設計ポイント
- 履歴保存（append_turn）と CRM 書き込みは応答クリティカルパスから外す（fire-and-forget / コンタクト終了時）。
- ベクトル検索キャッシュミス時のスキャン遅延を見越し、`top_k` と Claude `max_tokens` を予算から逆算。
- PII マスク後のテキストのみをログ・DynamoDB・Bedrock へ渡す（SECURITY-03）。

---

## 3. KnowledgeService（ナレッジ更新パイプライン）

**目的**: 週次クロール → 差分検出 → S3 保存 → 埋め込み → ベクトル upsert。

### オーケストレーションフロー

```
EventBridge Scheduler（日曜 02:00 JST）
  ▼
[CrawlerLambda]
  ├─ RobotsTxtGuard.load / is_allowed
  ├─ crawl_site()（1〜3 秒ディレイ）
  ├─ ContentParser.parse → list[ContentChunk]
  ├─ S3ContentStore.put（クロール本文を S3 保存）
  ├─ DifferEngine.diff → DiffResult（added/changed/deleted）
  └─ DifferEngine.commit（ContentDiff にハッシュ状態反映）
  ▼  （DiffResult を S3 に置き、参照を非同期イベント／直接 invoke で受け渡し）
[EmbedderLambda]
  ├─ added/changed を BedrockClient.embed（Titan v2）
  ├─ VectorStore.upsert
  └─ deleted を VectorStore.delete
```

### 通信パターン
- CrawlerLambda → EmbedderLambda: 大きな DiffResult は S3 経由で受け渡し、起動は EventBridge カスタムイベント（`knowledge.diff.ready`）または直接 `Invoke`（非同期 `Event` 型）。
- バッチのため同期応答時間制約なし。Lambda タイムアウトは余裕を持たせる（例: 15 分）。

---

## 4. ImprovementService（自己改善パイプライン）

**目的**: 低品質コンタクト抽出 → ギャップ分析 → 改善提案生成（週次最大 10 件）。

### オーケストレーションフロー

```
EventBridge Scheduler（週次）
  ▼
[ContactLensAnalyzerLambda]
  ├─ Contact Lens 出力取得
  ├─ extract_low_quality（CSAT ≤ 2 OR エスカレーション）
  └─ ContactAnalysis テーブルへ保存
  ▼  （EventBridge カスタムイベント `improvement.analysis.ready`）
[GapAnalyzerLambda]
  ├─ BedrockClient.analyze_gap（Claude）
  └─ 不足/不明瞭カテゴリ分類・わかりにくさスコア付与
  ▼
[SuggestionGeneratorLambda]
  ├─ スコア順位付け → 上位 10 件
  ├─ BedrockClient.generate_answer（改善提案文生成）
  └─ ImprovementSuggestions へ保存（status="pending"）
  ▼
DashboardService 経由で運用者が承認/却下
```

### 通信パターン
- 3 Lambda はステージ毎に EventBridge カスタムイベントで連鎖（疎結合）。
- 各ステージ独立リトライ可能（冪等キー = 対象週 `week_start`）。

---

## 5. SessionService（チャネル切り替え・文脈引き継ぎ）

**目的**: 音声⇔チャット切替時に同一 ContactId をキーに文脈を引き継ぐ。

### オーケストレーションフロー

```
Connect チャネル切替イベント
  ▼
[ChannelSwitchLambda]
  ├─ SessionContextManager.get(contact_id)
  ├─ SessionContextManager.summarize(last_n=5)
  └─ 引き継ぎ要約を新チャネルのプロンプト初期コンテキストへ注入
```

### 通信パターン
- 同期（Connect Lambda 統合）。8 秒制約適用。
- セッション文脈は CustomerHistory テーブル（contact_id をキーとするパーティション）で共有。

---

## 6. DashboardService（統計・提案管理 API）

**目的**: 運用者向けの改善提案管理と利用統計閲覧。

### オーケストレーションフロー

```
React (Amplify) → API Gateway（Cognito オーソライザ）
  ▼
[DashboardApiLambda]
  ├─ GET  /suggestions          → list_suggestions（ImprovementSuggestions）
  ├─ PATCH /suggestions/{id}     → update_suggestion_status
  └─ GET  /metrics              → MetricsAggregatorLambda 呼び出し（同期 Invoke）
                                    or 事前集計キャッシュ参照
```

### 通信パターン
- 同期 REST。API Gateway アクセスログ有効（SECURITY-02）。
- MetricsAggregatorLambda は週次 EventBridge での事前集計 + オンデマンド再集計の二系統。

---

## 7. サービス間通信パターン総括

| パターン | 利用箇所 | 理由 |
|---|---|---|
| **同期 Lambda 統合**（Connect → Lambda） | ConversationService, SessionService | 対話のリアルタイム性（8 秒制約） |
| **同期 Invoke**（Lambda → Lambda, RequestResponse） | DashboardApiLambda → MetricsAggregatorLambda | 即時レスポンスが必要 |
| **非同期 Invoke / EventBridge カスタムイベント** | KnowledgeService, ImprovementService のステージ連鎖 | バッチ・疎結合・独立リトライ |
| **EventBridge Scheduler** | KnowledgeService, ImprovementService, MetricsAggregator の起動 | 週次スケジュール |
| **S3 経由ペイロード受け渡し** | CrawlerLambda → EmbedderLambda（DiffResult） | Lambda ペイロードサイズ制約回避 |
| **DynamoDB 共有テーブル** | 各サービスの状態共有（履歴・提案・分析） | サーバレス・低運用 |

> 本設計では SQS を必須とはせず、非同期パイプラインは EventBridge カスタムイベント + Lambda 非同期呼び出しの自動リトライ + DLQ を基本とする。スループット増大時は EmbedderLambda 前段に SQS（バッチサイズ制御）を追加可能とする拡張ポイントを残す。

---

## 8. エラーハンドリング戦略

### 8.1 リトライ

| 対象 | 戦略 |
|---|---|
| Bedrock スロットリング（`BedrockThrottledError`） | 指数バックオフ + ジッタ（最大 3 回） |
| DynamoDB（`DynamoAccessError`） | boto3 標準リトライ（adaptive） |
| 非同期 Lambda 呼び出し失敗 | Lambda 非同期呼び出しの自動リトライ（最大 2 回） |
| CRM API（`CrmApiError`） | アプリ層で最大 3 回リトライ後 DLQ |

### 8.2 DLQ（Dead Letter Queue）

| Lambda | DLQ | 用途 |
|---|---|---|
| `CrawlerLambda` | SQS DLQ | クロール失敗の隔離・再処理 |
| `EmbedderLambda` | SQS DLQ | 埋め込み/upsert 失敗 |
| `ContactLensAnalyzerLambda` / `GapAnalyzerLambda` / `SuggestionGeneratorLambda` | SQS DLQ（各々） | 週次バッチ失敗の隔離 |
| `CrmWriterLambda` | SQS DLQ | CRM 書き込み恒久失敗 |

> 同期系（RagHandlerLambda 等）は DLQ ではなくフォールバック応答 + エスカレーションで対応。

### 8.3 フォールバック（同期・対話系）

- `TimeoutBudgetExceeded` / `BedrockError`: 「ただいま回答を生成できませんでした。オペレーターにおつなぎします」定型応答を返し、`EscalationLambda` へ誘導。
- `hit == False`（ナレッジ未ヒット）: 同様にエスカレーション。

### 8.4 アラーム（CloudWatch Alarms）

| メトリクス | しきい値 | アクション |
|---|---|---|
| Lambda `Errors`（各関数） | > 0（バッチ）/ 率ベース（同期） | SNS 通知 |
| Lambda `Duration` p99（RagHandler） | > 7.5s（8 秒制約の前段） | SNS 通知 |
| Lambda `Throttles` | > 0 | SNS 通知 |
| DLQ `ApproximateNumberOfMessagesVisible` | > 0 | SNS 通知（要手動再処理） |
| Bedrock スロットリング率 | しきい値超過 | SNS 通知 |
| API Gateway 5xx 率 | しきい値超過 | SNS 通知 |

> 全ログは構造化 JSON、PII を含めない（SECURITY-03）。アラーム通知は SNS → 運用チャネル。
