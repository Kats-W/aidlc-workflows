# Unit of Work（作業単位定義）— au Jibun Bank AI Agent

本ドキュメントは Units Generation フェーズの成果物であり、システムを 7 つの実装可能なユニット（U-01〜U-07）へ分解し、各ユニットのスコープ・担当コンポーネント・インターフェース・受け入れ完了条件（Definition of Done, 以下 DoD）を定義する。

- 関連: `components.md`（コンポーネント一覧）、`component-methods.md`（メソッドシグネチャ・例外型）、`component-dependency.md`（依存・データモデル）、`services.md`（サービスオーケストレーション）、`execution-plan.md`（実行計画）。
- 依存関係の詳細は `unit-of-work-dependency.md`、ストーリー対応は `unit-of-work-story-map.md` を参照。

---

## 1. コード組織化方針（確定: A案）

実装はモノレポ構成とし、ユニットごとに Python パッケージ（`src/<pkg>/`）を割り当てる。CDK スタックは `infra/stacks/` にユニット 1 対 1 で配置し、テストは `tests/unit/<pkg>/` + `tests/integration/` 構成とする。

```
src/
  crawler/               # U-02: CrawlerLambda, RobotsTxtGuard, ContentParser, DifferEngine, S3ContentStore
  vector_store/          # U-02: EmbedderLambda, VectorStore, CosineSimilaritySearcher
  rag_handler/           # U-03: RagHandlerLambda, Personalizer, EscalationLambda
  session_manager/       # U-03/U-04: HistoryRepository, CsatHandlerLambda, ChannelSwitchLambda, SessionContextManager
  profile/               # U-05: CustomerProfileLambda, CrmWriterLambda, IdentityHasher
  improvement_generator/ # U-06: ContactLensAnalyzerLambda, GapAnalyzerLambda, SuggestionGeneratorLambda
  dashboard_api/         # U-07: DashboardApiLambda, MetricsAggregatorLambda
  common/                # 共通: BedrockClient, PiiMasker, AppError 例外型
infra/
  stacks/
    shared_infra_stack.py        # U-01
    knowledge_pipeline_stack.py  # U-02
    conversation_stack.py        # U-03
    omnichannel_stack.py         # U-04
    profile_stack.py             # U-05
    improvement_stack.py         # U-06
    dashboard_stack.py           # U-07
  app.py
frontend/                # U-07 React (Amplify)
  src/
    components/
    api/
tests/
  unit/
    crawler/
    vector_store/
    rag_handler/
    session_manager/
    profile/
    improvement_generator/
    dashboard_api/
    common/
  integration/
    test_knowledge_pipeline.py
    test_conversation_engine.py
    test_omnichannel.py
    test_improvement_pipeline.py
pyproject.toml
```

### 横断的な実装規約
- 言語/ランタイム: Python 3.12（Lambda）、TypeScript（CDK / React）。
- I/O は `async def`（`aioboto3` / `asyncio`）、CPU バウンド純粋計算（コサイン類似度等）は同期。
- 例外は `src/common/` の `AppError` 階層に統一（`component-methods.md` 末尾参照）。全 Lambda ハンドラは `AppError` を捕捉し構造化 JSON ログ（PII 除外）を出力。
- テスト: 各 `src/<pkg>/` に対応する `tests/unit/<pkg>/`。純粋計算・ハッシュ・マスク・差分判定など入力空間が広いロジックは Property-Based Testing（`hypothesis`）を必須とする。カバレッジ目標は内部コードパス 80% 以上。

---

## 2. ユニット定義

### 凡例
- 規模: S（小）/ M（中）/ L（大）。
- DoD は「コード + 単体テスト + CDK デプロイ可能 + ストーリー受け入れ基準充足」を最低条件とし、各ユニット固有条件を追加列挙する。

---

### U-01: Core Infrastructure

| 項目 | 内容 |
|---|---|
| 名称 | Core Infrastructure（共通基盤） |
| 規模 | M |
| 依存 | なし |
| CDK Stack | `SharedInfraStack`（`infra/stacks/shared_infra_stack.py`） |
| コードパッケージ | `src/common/`（AppError 例外型の初期定義のみ。Bedrock/Pii は U-02/U-03 で拡充） |

**説明・目的**
全ユニットが共有する基盤リソースを一括定義する。DynamoDB 全 5 テーブル、IAM 境界、Secrets Manager、KMS、ログ基盤、および Amazon Connect インスタンス・Lex v2 ボット定義（外枠）を提供し、後続ユニットがリソース参照のみで構築できる状態を作る。

**担当コンポーネント**
- DynamoDB（全 5 テーブルを U-01 で作成）:
  1. **VectorStore**: PK=`chunkId`、`chunkText` / `sourceUrl` / `embedding`(Binary) / `contentHash`、GSI=`gsi_sourceUrl`
  2. **CustomerHistory**: PK=`customerId`、SK=`TURN#timestamp` 等、`channel` / `masked_text` / `ttl`(90日)、GSI=`gsi_contactId`
  3. **ImprovementSuggestions**: PK=`weekStart`、SK=`suggestionId`、`targetUrl` / `suggestion` / `priorityScore` / `status`
  4. **ContentDiff**: PK=`sourceUrl`、SK=`chunkId`、`contentHash` / `lastCrawledAt`
  5. **ContactAnalysis**: PK=`weekStart`、SK=`contactId`、`csatScore` / `escalated` / `gaps`
- IAM: 最小権限ロール境界・権限境界ポリシー
- Secrets Manager: CRM API キー（U-05 が参照）、Claude プロンプト管理シークレット
- KMS: 保存時暗号化用カスタマーマネージドキー（DynamoDB / S3 / Logs 共用）
- CloudWatch Logs: 構造化 JSON ログ前提のロググループ・保持期間
- Amazon Connect インスタンス（インフラ枠）、Lex v2 ボット定義（外枠）

**スコープ内 / スコープ外**
- 内: 全 DynamoDB テーブル定義、IAM/Secrets/KMS/Logs、Connect インスタンス・Lex ボット骨格、`AppError` 例外基底の定義。
- 外: 各 Lambda 実装（U-02 以降）、Connect コンタクトフロー本体（U-03/U-04）、Lex インテント詳細（U-03）。

**入力 / 出力インターフェース**
- 入力: CDK context（`env=dev/staging/prod`）。
- 出力（CloudFormation Export / SSM）: 5 テーブル名・ARN、各 GSI 名、Connect インスタンス ARN、Lex ボット ID、KMS Key ARN、Secret ARN 群、ロググループ ARN。

**Definition of Done**
- [ ] 5 テーブルすべてが PK/SK/GSI/TTL 設定どおりに `cdk deploy` 成功（dev）。
- [ ] **Security Extension の全ルールを適用しブロッキング 0 件**（保存時暗号化 KMS、転送時 TLS 1.2 以上、IAM 最小権限、PII をログに残さない設定、CloudTrail/アクセスログ有効）。
- [ ] DynamoDB / S3 / Logs が KMS で暗号化されていることをテストで検証。
- [ ] Connect インスタンス・Lex ボット骨格がデプロイされ ARN/ID が Export される。
- [ ] `AppError` 例外階層が `src/common/` に定義され単体テスト合格。

**技術的メモ**
- DynamoDB は On-Demand 課金を基本（100 セッション未満で月額 ≤ 5,000 円のコスト目標）。
- VectorStore の `embedding` は Binary 格納（Titan v2: 1024 次元）。GSI は必要最小限（コスト抑制）。
- Connect/Lex IaC は CDK で管理（L1 Construct による JSON import）。

---

### U-02: Knowledge Pipeline

| 項目 | 内容 |
|---|---|
| 名称 | Knowledge Pipeline（クローリング + 埋め込み + ベクトルストア） |
| 規模 | L |
| 依存 | U-01 |
| CDK Stack | `KnowledgePipelineStack`（`knowledge_pipeline_stack.py`） |
| コードパッケージ | `src/crawler/`, `src/vector_store/`, `src/common/`（BedrockClient.embed） |

**説明・目的**
au じぶん銀行公式サイト・FAQ を週次でクローリングし、差分のみを Titan Embeddings v2 でベクトル化して VectorStore に反映する。RAG 検索の基盤となるコサイン類似度検索（`/tmp` キャッシュ付き）を提供する。

**担当コンポーネント**
- Lambda: `CrawlerLambda`、`EmbedderLambda`
- Internal Module: `RobotsTxtGuard`, `ContentParser`, `DifferEngine`, `S3ContentStore`, `VectorStore`, `CosineSimilaritySearcher`
- DynamoDB 操作: `VectorStore`（upsert/delete/scan_all）、`ContentDiff`（diff/commit）
- AWS: EventBridge Scheduler（週次クロール: 日曜 02:00 JST）、S3（クロールコンテンツ）、Bedrock Titan Embeddings v2

**スコープ内 / スコープ外**
- 内: クロール（robots.txt 遵守・1〜3 秒ディレイ）、本文抽出・チャンク分割（約500トークン/10%オーバーラップ）、SHA-256 差分検出、S3 保存/削除、ベクトル化 upsert/delete、コサイン検索。
- 外: RAG 回答生成（U-03）、Claude 呼び出し（U-03）、PII マスク（U-03）。

**入力 / 出力インターフェース**
- 入力: EventBridge Scheduler イベント（週次）、S3 イベント（Embedder トリガー）。
- 出力: `CrawlerLambda` → `{crawled, added, changed, deleted, errors[]}`、`EmbedderLambda` → `{upserted, deleted}`、`CosineSimilaritySearcher.search()` → `list[SearchHit]`。

**Definition of Done**
- [ ] US-2.1 / US-2.2 / US-2.3 の受け入れ基準を充足。
- [ ] robots.txt の Disallow を遵守し、許可ページのみクロール（`RobotsDisallowedError` を適切に処理）。
- [ ] SHA-256 差分により未変更ページをスキップ、削除ページの VectorStore レコードを全削除。
- [ ] **コストボトルネック対策: `CosineSimilaritySearcher` が VectorStore 全件スキャン結果を `/tmp` キャッシュ（TTL 15分）に保持し、キャッシュ有効時は `scan_all()` を呼ばないことをテストで検証**（スキャン頻度抑制）。
- [ ] 1024 次元ベクトルの upsert / 削除整合性をテスト。
- [ ] `_cosine` を中心に Property-Based Testing（`hypothesis`）で検証（対称性・自己類似度=1.0・範囲 [-1,1]、ハッシュ安定性、差分判定の冪等性）。

**技術的メモ**
- Lambda 最大実行 15 分（大規模サイトは Step Functions 分割を将来検討）。1 ページ上限 1MB、1 ページ最大 20 チャンク。
- 全件スキャンは RCU コスト要因のため `/tmp` キャッシュで頻度を抑制。Embedder の Bedrock 呼び出しはバッチ化・指数バックオフ（`BedrockThrottledError`）。

---

### U-03: AI Conversation Engine

| 項目 | 内容 |
|---|---|
| 名称 | AI Conversation Engine（RAG + PII + 履歴 + CSAT） |
| 規模 | L |
| 依存 | U-01, U-02 |
| CDK Stack | `ConversationStack`（`conversation_stack.py`） |
| コードパッケージ | `src/rag_handler/`, `src/session_manager/`（HistoryRepository, CsatHandlerLambda）, `src/common/`（BedrockClient, PiiMasker） |

**説明・目的**
Connect コンタクトフローのフックとして、PII マスク → 履歴サマリー注入 → ベクトル検索 → Claude 回答生成 → 出典付与 → 履歴保存を 8 秒以内に実行する。RAG 中核とパーソナライズ、CSAT 記録を担う。

**担当コンポーネント**
- Lambda: `RagHandlerLambda`、`PersonalizerLambda`、`CsatHandlerLambda`
- Internal Module: `BedrockClient`（Claude claude-sonnet-4-6 / Titan）、`PiiMasker`（Comprehend）、`HistoryRepository`
- DynamoDB 操作: `CustomerHistory`（append_turn / get_recent / save_summary、TTL 90日）、`VectorStore` 経由でコサイン検索（U-02 提供）
- AWS: Bedrock Claude claude-sonnet-4-6（RAG）、Titan Embeddings v2（クエリ埋め込み）、Polly Neural Kazuha（TTS）、Comprehend（PII）、Connect コンタクトフロー本体、Lex v2 インテント設定

**スコープ内 / スコープ外**
- 内: RAG パイプライン、PII 検出/マスク、直近5件履歴サマリー注入、出典 URL 付与、CSAT 記録、Connect フロー/Lex インテント実装、Polly 音声合成連携。
- 外: エスカレーション転送ロジック（`EscalationLambda` は `src/rag_handler/` に配置するが転送先設定は U-04）、チャネル切替（U-04）、CRM 書き込み（U-05）、改善分析（U-06）。

**入力 / 出力インターフェース**
- 入力: `ConnectEvent`（`Details.ContactData` に `customerId` / `userInput`）。
- 出力: `RagHandlerLambda` → `{answer, sources[], hit}`、`PersonalizerLambda` → `{summary, turn_count}`、`CsatHandlerLambda` → `{saved, contact_id, score}`。

**Definition of Done**
- [ ] US-1.1 / US-1.2 / US-1.3 / US-1.4 / US-6.1 / US-6.2 の受け入れ基準を充足。
- [ ] **PII マスク必須: Comprehend で氏名→[NAME]・口座番号→[ACCOUNT] 等を検出マスクし、CustomerHistory に保存する `masked_text` および Claude へ送るテキストが PII を含まないことをテストで検証**。生 PII はログ・保存いずれにも出さない。
- [ ] **Lambda 8 秒タイムアウト制約: タイムアウト予算（閾値 6 秒）超過時に `TimeoutBudgetExceeded` でフォールバック応答（保留メッセージ）を返し、P99 ≤ 5 秒を満たすことを検証**。
- [ ] **コストボトルネック: クエリ時のベクトル検索は U-02 の `/tmp` キャッシュ（15分 TTL）を利用し、全件スキャンを毎回実行しないことを確認**。
- [ ] 確信度 < 0.4 またはユーザー「人と話したい」発話時に `hit=False`（エスカレーション候補）を返す。
- [ ] CustomerHistory への追記が TTL 90 日付きで保存され、`get_recent(limit=5)` が降順取得（P99 ≤ 100ms 目標、プロジェクション指定）。
- [ ] anonymous ユーザーは履歴保存しない。
- [ ] `PiiMasker.mask` / `_cosine` 等を Property-Based Testing（`hypothesis`）で検証（任意入力でマスク後に PII パターンが残存しない不変条件）。

**技術的メモ**
- Connect 同期 Lambda は 8 秒制約。Polly/Comprehend/Bedrock の合算レイテンシを 6 秒予算で管理。
- 履歴サマリーはプロンプト内最大 500 トークンに切り詰め。Bedrock は `BedrockThrottledError` を指数バックオフでリトライ。

---

### U-04: Omnichannel & Escalation

| 項目 | 内容 |
|---|---|
| 名称 | Omnichannel & Escalation（チャネル継続 + エスカレーション） |
| 規模 | M |
| 依存 | U-01, U-03 |
| CDK Stack | `OmnichannelStack`（`omnichannel_stack.py`） |
| コードパッケージ | `src/session_manager/`（ChannelSwitchLambda, SessionContextManager）, `src/rag_handler/`（EscalationLambda の転送設定） |

**説明・目的**
音声⇔チャット切り替え時に同一 ContactId をキーに会話文脈を引き継ぎ、ナレッジ未ヒット時に有人キューへエスカレーションする。有人対応後の AI 復帰・CSAT 誘導も担う。

**担当コンポーネント**
- Lambda: `ChannelSwitchLambda`、`EscalationLambda`
- Internal Module: `SessionContextManager`
- DynamoDB 操作: `CustomerHistory`（SessionContextManager 経由でターン取得・要約更新）
- AWS: Connect チャネル設定・発信コンタクトフロー・有人キュー転送

**スコープ内 / スコープ外**
- 内: 音声→チャット / チャット→音声切替（ContactId キー文脈引き継ぎ、直近5ターン要約注入）、エスカレーション転送属性設定（`queue_arn`）、有人後 AI 復帰時の CSAT 誘導ルーティング。
- 外: RAG 回答生成・CSAT 記録本体（U-03）、PII マスク（U-03）。

**入力 / 出力インターフェース**
- 入力: `ConnectEvent`（チャネル切替 / escalate フラグ）。
- 出力: `ChannelSwitchLambda` → `{handover_summary, channel_from, channel_to}`、`EscalationLambda` → `{escalate, queue_arn|None, reason}`。

**Definition of Done**
- [ ] US-4.1 / US-4.2 / US-4.3 の受け入れ基準を充足。
- [ ] 同一 ContactId で直近 5 ターン要約が引き継がれることをテスト（`SessionNotFoundError` の境界処理含む）。
- [ ] エスカレーション時に `escalated=true` 属性・会話サマリー JSON がコンタクト属性へ添付される。
- [ ] 有人後の「AI へ転送」で CSAT フロー（U-03）へリルーティングされる。
- [ ] 切替後 5 分でセッション破棄、電話番号バリデーション（日本フォーマット）を検証。
- [ ] 要約生成・電話番号正規表現を Property-Based Testing（`hypothesis`）で検証。

**技術的メモ**
- `EscalationLambda` のコードは `src/rag_handler/` に同居（確定組織化）だが、キュー/転送設定の責務は U-04。キュー未設定時は `ConfigError`。
- Push 通知失敗時は SMS/バッジ代替。

---

### U-05: SDK & Customer Profile

| 項目 | 内容 |
|---|---|
| 名称 | SDK & Customer Profile（プロファイル連携 + CRM 書き込み） |
| 規模 | M |
| 依存 | U-01, U-03 |
| CDK Stack | `ProfileStack`（`profile_stack.py`） |
| コードパッケージ | `src/profile/`（CustomerProfileLambda, CrmWriterLambda, IdentityHasher） |

**説明・目的**
au ID ハッシュ（SHA-256）から顧客属性を解決して Connect 属性へ付与し、コンタクト終了時に会話サマリーを CRM API へ書き込む（Secrets Manager 認証）。ネイティブアプリ Chat SDK 連携の顧客プロファイル基盤を担う。

**担当コンポーネント**
- Lambda: `CustomerProfileLambda`、`CrmWriterLambda`
- Internal Module: `IdentityHasher`
- DynamoDB 操作: `CustomerHistory`（customerId キーでプロファイル参照）
- AWS: Connect Customer Profiles、Secrets Manager（CRM API キー）、SQS DLQ

**スコープ内 / スコープ外**
- 内: au ID ハッシュ生成（クライアント側ハッシュ前提の検証）、顧客属性解決・Connect 属性付与、CRM 非同期 POST（リトライ最大3回・DLQ）。
- 外: 会話サマリー生成本体（U-03）、PII マスク（U-03）、Chat SDK ネイティブ実装（アプリ側、スコープ外 API 呼び出し口のみ）。

**入力 / 出力インターフェース**
- 入力: `ConnectEvent`（au ID ハッシュ）、CRM 書き込みイベント（customerId, summary）。
- 出力: `CustomerProfileLambda` → `{customer_id, tier|None, found}`、`CrmWriterLambda` → `{written, crm_record_id|None}`。

**Definition of Done**
- [ ] US-5.1 / US-5.2 / US-6.3 の受け入れ基準を充足。
- [ ] 平文 au ID をサーバー/ログに残さない（`IdentityHasher.hash_au_id`、空入力で `ValidationError`）。
- [ ] 未ログインは `customerId="anonymous"` で扱い、anonymous は CRM 書き込みをスキップ。
- [ ] CRM 認証情報は Secrets Manager から取得（`SecretsError` 処理）、4xx/5xx は指数バックオフ最大3回後 DLQ・アラーム（`CrmApiError`）。
- [ ] `IdentityHasher.hash_au_id` を Property-Based Testing（`hypothesis`）で検証（決定性・同一入力同一ハッシュ・平文非露出）。

**技術的メモ**
- ハッシュは原則クライアント実行。サーバー側 `IdentityHasher` は検証/再計算補助用途。
- CRM はスコープ外システムのため呼び出し口のみ実装。

---

### U-06: Self-Improvement Pipeline

| 項目 | 内容 |
|---|---|
| 名称 | Self-Improvement Pipeline（自己改善サイクル） |
| 規模 | L |
| 依存 | U-01, U-03 |
| CDK Stack | `ImprovementStack`（`improvement_stack.py`） |
| コードパッケージ | `src/improvement_generator/`（ContactLensAnalyzerLambda, GapAnalyzerLambda, SuggestionGeneratorLambda）, `src/common/`（BedrockClient.analyze_gap） |

**説明・目的**
週次で低品質コンタクト（CSAT≤2 / エスカレーション / NEGATIVE）を Contact Lens から抽出し、Claude でナレッジギャップを分析、わかりにくさスコア上位の改善提案を最大 10 件生成・保存する。

**担当コンポーネント**
- Lambda: `ContactLensAnalyzerLambda`、`GapAnalyzerLambda`、`SuggestionGeneratorLambda`
- DynamoDB 操作: `ContactAnalysis`（保存）、`ImprovementSuggestions`（提案 upsert、重複スキップ）、`CustomerHistory`（サマリー参照）
- AWS: Connect Contact Lens、Bedrock Claude claude-sonnet-4-6（Gap 分析）、EventBridge Scheduler（週次月曜 03:00 JST）

**スコープ内 / スコープ外**
- 内: 低品質抽出（指数バックオフ最大3回）、Claude ギャップ分類・わかりにくさスコア算出、最大10件提案生成（status=pending）、未対応提案の重複生成スキップ。
- 外: 改善提案の閲覧/承認 UI・API（U-07）、PII マスク本体（U-03、ここではマスク済みサマリーのみ使用）。

**入力 / 出力インターフェース**
- 入力: EventBridge（週次）。
- 出力: `ContactLensAnalyzerLambda` → `{analyzed, low_quality}`、`GapAnalyzerLambda` → `{gaps[], count}`、`SuggestionGeneratorLambda` → `{generated}`（0〜10）。

**Definition of Done**
- [ ] US-3.1 / US-3.2 / US-3.3 の受け入れ基準を充足。
- [ ] Claude へ送るテキストは PII マスク済みサマリーのみ（生会話テキスト不使用）を検証。
- [ ] 低品質 0 件時はスキップし「改善提案なし」を記録。
- [ ] 提案は最大 10 件、未対応（pending）の同一 URL 提案は重複生成スキップ、TTL 90 日。
- [ ] Contact Lens / Bedrock のレート制限に指数バックオフ最大3回（`ContactLensError` / `BedrockThrottledError`）。
- [ ] わかりにくさスコア算出・上位 N 選定を Property-Based Testing（`hypothesis`）で検証（単調性・上限10件）。

**技術的メモ**
- カテゴリ分類プロンプトは Secrets Manager 管理（バージョン管理）。`ResponseParseError` を捕捉。

---

### U-07: Admin Dashboard

| 項目 | 内容 |
|---|---|
| 名称 | Admin Dashboard（管理ダッシュボード） |
| 規模 | M |
| 依存 | U-01, U-06 |
| CDK Stack | `DashboardStack`（`dashboard_stack.py`） |
| コードパッケージ | `src/dashboard_api/`（DashboardApiLambda, MetricsAggregatorLambda）, `frontend/`（React Amplify, ApiClient） |

**説明・目的**
Amplify + React で構築する管理ダッシュボード。週次改善提案の確認・承認/却下/保留、利用統計の可視化、Cognito 認証アクセス制御を提供する。

**担当コンポーネント**
- Lambda: `DashboardApiLambda`（GET /suggestions, PATCH /suggestions/{id}, GET /metrics）、`MetricsAggregatorLambda`
- React: `App`, `LoginView`, `SuggestionListView`, `SuggestionStatusControl`, `MetricsView`、`ApiClient`（TS）
- DynamoDB 操作: `ImprovementSuggestions`（一覧/ステータス更新）、`CustomerHistory` / `ContactAnalysis`（メトリクス集計）
- AWS: API Gateway（Cognito オーソライザ）、Cognito ユーザープール、AWS Amplify ホスティング

**スコープ内 / スコープ外**
- 内: 提案一覧（優先度降順・最大12週・ページング・CSV）、承認/却下/保留更新、利用統計（件数・チャネル比・エスカレーション率・平均CSAT・平均ターン・AI解決率）、Cognito 認証（MFA オプション・トークンリフレッシュ）。
- 外: 提案生成（U-06）、メトリクス元データ生成（U-03/U-04）。

**入力 / 出力インターフェース**
- 入力: API Gateway（Cognito 認証済み HTTP）、EventBridge（週次集計）。
- 出力: `DashboardApiLambda` → `{statusCode, body}`、`MetricsAggregatorLambda` → `{period, contacts, channel_ratio, escalation_rate, avg_turns}`。

**Definition of Done**
- [ ] US-7.1 / US-7.2 / US-7.3 の受け入れ基準を充足。
- [ ] 未認証は Cognito Hosted UI へリダイレクト、認証後にダッシュボード表示、トークン自動リフレッシュ。
- [ ] 提案承認/却下が `ImprovementSuggestions` に反映・更新日時記録（`NotFoundError` / `ValidationError` / `UnauthorizedError` 処理）。
- [ ] 統計が直近7日/30日切替で 3 秒以内に表示、レスポンシブ対応。
- [ ] `ApiClient` 401 時に再認証誘導。`MetricsAggregatorLambda` 集計ロジックを単体テスト（境界: データ0件）。

**技術的メモ**
- グラフは Recharts。CSV エクスポート対応。API はアクセスログ有効（SECURITY-02）。

---

## 3. CDK スタック対応表

| スタック | ファイル | ユニット | 主リソース | デプロイ前提 |
|---|---|---|---|---|
| `SharedInfraStack` | `shared_infra_stack.py` | U-01 | DynamoDB×5, IAM, Secrets, KMS, Logs, Connect インスタンス, Lex ボット骨格 | なし |
| `KnowledgePipelineStack` | `knowledge_pipeline_stack.py` | U-02 | CrawlerLambda, EmbedderLambda, S3, EventBridge Scheduler | U-01 |
| `ConversationStack` | `conversation_stack.py` | U-03 | RagHandlerLambda, PersonalizerLambda, CsatHandlerLambda, Connect フロー, Lex インテント, Polly | U-01, U-02 |
| `OmnichannelStack` | `omnichannel_stack.py` | U-04 | ChannelSwitchLambda, EscalationLambda, チャネル/発信フロー, 有人キュー | U-01, U-03 |
| `ProfileStack` | `profile_stack.py` | U-05 | CustomerProfileLambda, CrmWriterLambda, SQS DLQ, Customer Profiles | U-01, U-03 |
| `ImprovementStack` | `improvement_stack.py` | U-06 | ContactLensAnalyzerLambda, GapAnalyzerLambda, SuggestionGeneratorLambda, EventBridge Scheduler | U-01, U-03 |
| `DashboardStack` | `dashboard_stack.py` | U-07 | DashboardApiLambda, MetricsAggregatorLambda, API Gateway, Cognito, Amplify | U-01, U-06 |

---

## 4. 実装シーケンス

確定実装順序:

```
U-01 (Core Infrastructure)
  └─> U-02 (Knowledge Pipeline)
        └─> U-03 (AI Conversation Engine)
              ├─> U-04 (Omnichannel & Escalation)   ← U-05 より先に実装
              ├─> U-05 (SDK & Customer Profile)
              └─> U-06 (Self-Improvement Pipeline)
                    └─> U-07 (Admin Dashboard)
```

直列順序: **U-01 → U-02 → U-03 → U-04（先） → U-05 → U-06 → U-07**

- U-04 / U-05 は U-03 完了後に並列開発可能だが、実装着手は U-04 を先行させる。
- U-07 は U-06 の `ImprovementSuggestions` 生成と U-03/U-04 のメトリクス元データに依存するため最後。
