# Components — au Jibun Bank AI Agent

本ドキュメントは Application Design フェーズの成果物であり、全コンポーネントの一覧・分類・責務・主要インターフェース（入出力概要）を定義する。詳細なメソッドシグネチャは `component-methods.md`、サービスオーケストレーションは `services.md`、依存関係・データモデルは `component-dependency.md` を参照すること。

---

## 1. コンポーネント分類の定義

| 分類 | 説明 | 実装言語 |
|---|---|---|
| **Lambda Function** | AWS Lambda にデプロイされるビジネスロジック実行単位（ハンドラ + 内部モジュール） | Python 3.12 |
| **Internal Module** | Lambda 関数内で利用される再利用可能なクラス／ユーティリティ（`src/common` 等） | Python 3.12 |
| **CDK Construct / Stack** | インフラ定義（AWS CDK v2） | TypeScript |
| **React Component** | 管理ダッシュボードの UI コンポーネント | TypeScript (React) |
| **DynamoDB Table** | データストア（論理エンティティ） | — |

> 命名規約: コンポーネント名は UpperCamelCase、メソッド名は snake_case（Python）。

---

## 2. ユニット別コンポーネント一覧

### U-01: Core Infrastructure（CDK 基盤スタック）

| コンポーネント | 分類 | 責務 | 主要インターフェース（入出力） |
|---|---|---|---|
| `CoreInfraStack` | CDK Stack | VPC（任意）・KMS キー・共通 IAM 境界・CloudTrail・タグ戦略・環境 context 解決 | 入力: CDK context（`env=dev/staging/prod`） / 出力: KMS Key ARN, 共通 SSM パラメータ |
| `KmsKeyConstruct` | CDK Construct | 保存時暗号化用カスタマーマネージドキー（DynamoDB / S3 / Logs 共用） | 出力: `kms.IKey` |
| `LoggingConstruct` | CDK Construct | CloudWatch Logs グループ（構造化 JSON ログ前提）・保持期間・暗号化 | 出力: LogGroup ARN 群 |
| `SecretsConstruct` | CDK Construct | Secrets Manager シークレット（CRM API クレデンシャル等）の定義と参照 | 出力: Secret ARN 群 |

### U-02: Knowledge Pipeline（クローラー + Embeddings + ベクトルストア）

| コンポーネント | 分類 | 責務 | 主要インターフェース（入出力） |
|---|---|---|---|
| `CrawlerLambda` | Lambda Function | 週次クローリングのオーケストレーション。robots.txt 遵守、1〜3 秒ランダムディレイ、ページ取得 | 入力: EventBridge Scheduler イベント / 出力: クロール結果サマリー（件数・エラー） |
| `RobotsTxtGuard` | Internal Module | `robots.txt` を取得・解析し、Disallow ルールに基づき URL を許可/拒否 | 入力: `base_url` / 出力: `is_allowed(url) -> bool` |
| `ContentParser` | Internal Module | HTML から本文テキスト抽出・正規化・チャンク分割・言語識別フィールド付与 | 入力: 生 HTML, URL / 出力: `list[ContentChunk]` |
| `DifferEngine` | Internal Module | 前回クロール結果（ContentDiff テーブル）との差分検出（追加/変更/削除） | 入力: 新チャンクハッシュ集合 / 出力: `DiffResult`（added/changed/deleted） |
| `EmbedderLambda` | Lambda Function | 差分チャンクを Titan Embeddings v2 でベクトル化し VectorStore へ upsert、削除分を除外 | 入力: `DiffResult`（S3 参照） / 出力: upsert/削除件数 |
| `VectorStore` | Internal Module | DynamoDB VectorStore テーブルへの upsert / delete / scan の抽象化 | 入力: チャンク+ベクトル / 出力: 書き込み結果 |
| `CosineSimilaritySearcher` | Internal Module | DynamoDB 全件スキャン + コサイン類似度検索（`/tmp` キャッシュ 15 分 TTL） | 入力: `query_vec`, `top_k` / 出力: `list[SearchHit]`（text, sourceUrl, score） |
| `S3ContentStore` | Internal Module | S3 へのクロール済みコンテンツ保存・取得・削除 | 入力: チャンク本文 / 出力: S3 オブジェクトキー |

### U-03: AI Conversation Engine（RAG + PII + CSAT + 履歴）

| コンポーネント | 分類 | 責務 | 主要インターフェース（入出力） |
|---|---|---|---|
| `RagHandlerLambda` | Lambda Function | Connect コンタクトフローフック。PII マスク→履歴注入→RAG 検索→Claude 回答生成→出典付与→履歴保存。8 秒制約遵守 | 入力: Connect Lambda イベント / 出力: `{answer, sources[]}` |
| `BedrockClient` | Internal Module | Bedrock Runtime ラッパ（Claude claude-sonnet-4-6 回答生成 + Titan Embeddings v2 埋め込み） | 入力: prompt / 出力: 生成テキスト, 埋め込みベクトル |
| `PiiMasker` | Internal Module | Amazon Comprehend で PII 検出しマスク（氏名・口座番号等） | 入力: 生テキスト / 出力: マスク済みテキスト, 検出エンティティ |
| `PersonalizerLambda` | Lambda Function | 直近 5 件の会話サマリーを取得しプロンプト用コンテキストを構築（RagHandler から内部利用または独立呼び出し） | 入力: `customer_id` / 出力: 履歴サマリーテキスト |
| `HistoryRepository` | Internal Module | CustomerHistory テーブルへのターン追記・履歴取得・サマリー保存（TTL 90 日） | 入力: `customer_id`, ターンデータ / 出力: 履歴アイテム |
| `CsatHandlerLambda` | Lambda Function | コンタクト終了時の CSAT アンケート結果を受領し DynamoDB へ記録 | 入力: Connect CSAT イベント / 出力: 保存結果 |

### U-04: Omnichannel & Escalation

| コンポーネント | 分類 | 責務 | 主要インターフェース（入出力） |
|---|---|---|---|
| `ChannelSwitchLambda` | Lambda Function | 音声⇔チャネル切り替え時に同一 ContactId をキーに文脈を引き継ぎ、直近 N ターン要約をプロンプトへ注入 | 入力: Connect チャネル切替イベント / 出力: 引き継ぎコンテキスト |
| `SessionContextManager` | Internal Module | コンタクトセッション文脈（ターン履歴・要約・チャネル状態）の保持・更新 | 入力: `contact_id` / 出力: `SessionContext` |
| `EscalationLambda` | Lambda Function | ナレッジ未ヒット時に有人オペレーターへエスカレーション。キュー転送・属性設定 | 入力: Connect イベント（escalate フラグ） / 出力: 転送指示属性 |

### U-05: SDK & Customer Profile

| コンポーネント | 分類 | 責務 | 主要インターフェース（入出力） |
|---|---|---|---|
| `CustomerProfileLambda` | Lambda Function | 匿名開始コンタクトに対し au ID ハッシュ（SHA-256）から顧客属性を付与（Connect Customer Profiles） | 入力: Connect イベント（au ID ハッシュ） / 出力: 顧客属性 |
| `CrmWriterLambda` | Lambda Function | コンタクト終了時に会話サマリーを CRM API へ書き込み（Secrets Manager で認証） | 入力: `customer_id`, サマリー / 出力: CRM 書き込み結果 |
| `IdentityHasher` | Internal Module | au ID の SHA-256 ハッシュ生成（平文 PII を Connect に渡さない） | 入力: au ID / 出力: ハッシュ文字列 |

### U-06: Self-Improvement Pipeline

| コンポーネント | 分類 | 責務 | 主要インターフェース（入出力） |
|---|---|---|---|
| `ContactLensAnalyzerLambda` | Lambda Function | Contact Lens 出力を取得し低品質コンタクト（CSAT ≤ 2 / エスカレーション）を抽出、ContactAnalysis へ保存 | 入力: EventBridge（週次） / 出力: 抽出コンタクト件数 |
| `GapAnalyzerLambda` | Lambda Function | 抽出コンタクトを Claude でナレッジギャップ分析し、不足/不明瞭カテゴリを分類・スコアリング | 入力: ContactAnalysis アイテム群 / 出力: ギャップ分類結果 |
| `SuggestionGeneratorLambda` | Lambda Function | ギャップを「わかりにくさスコア」で順位付けし上位 10 件の改善提案を生成、ImprovementSuggestions へ保存 | 入力: ギャップ分類結果 / 出力: 改善提案 ≤ 10 件 |

### U-07: Admin Dashboard（Amplify + React + Cognito）

| コンポーネント | 分類 | 責務 | 主要インターフェース（入出力） |
|---|---|---|---|
| `DashboardApiLambda` | Lambda Function | API Gateway 背後の API。改善提案一覧取得・ステータス更新、利用統計取得 | 入力: API Gateway（Cognito 認証） / 出力: JSON レスポンス |
| `MetricsAggregatorLambda` | Lambda Function | 週次コンタクト件数・チャネル別割合・エスカレーション率・平均ターン数を集計 | 入力: EventBridge（週次）または API 要求 / 出力: 集計メトリクス |
| `App` | React Component | ダッシュボード全体のルートコンポーネント（ルーティング・Cognito 認証ガード） | 入力: — / 出力: 画面描画 |
| `LoginView` | React Component | Cognito Hosted UI / Amplify Auth ログイン | 入力: 認証情報 / 出力: セッショントークン |
| `SuggestionListView` | React Component | 改善提案一覧（優先度スコア・対象 URL・提案内容・ステータス）表示 | 入力: API レスポンス / 出力: 一覧描画 |
| `SuggestionStatusControl` | React Component | 各提案の「承認/却下/保留」ステータス更新コントロール | 入力: 提案 ID, 新ステータス / 出力: API 呼び出し |
| `MetricsView` | React Component | 利用統計（件数・チャネル割合・エスカレーション率・平均ターン数）の可視化 | 入力: メトリクス API レスポンス / 出力: チャート描画 |
| `ApiClient` | Internal Module (TS) | DashboardApi への型付き HTTP クライアント（Cognito トークン付与） | 入力: リクエスト / 出力: 型付きレスポンス |

---

## 3. CDK スタック群（U-01〜U-07 横断）

技術環境ドキュメントのディレクトリ構成に整合させる。

| スタック | 所属ユニット | 責務 |
|---|---|---|
| `CoreInfraStack` | U-01 | KMS・ログ・シークレット・共通基盤 |
| `StorageStack` | U-01/U-02/U-03/U-06 | DynamoDB テーブル群（VectorStore, CustomerHistory, ImprovementSuggestions, ContentDiff, ContactAnalysis）・S3 バケット |
| `KnowledgeStack` | U-02 | CrawlerLambda / EmbedderLambda、EventBridge Scheduler（週次クロール） |
| `ConnectStack` | U-01/U-04 | Amazon Connect インスタンス・Lex v2 ボット・Polly 設定・コンタクトフロー |
| `AgentStack` | U-03/U-04/U-05 | RagHandlerLambda, PersonalizerLambda, CsatHandlerLambda, ChannelSwitchLambda, EscalationLambda, CustomerProfileLambda, CrmWriterLambda |
| `ImprovementStack` | U-06 | ContactLensAnalyzerLambda, GapAnalyzerLambda, SuggestionGeneratorLambda、EventBridge Scheduler（週次改善提案） |
| `DashboardStack` | U-07 | Amplify ホスティング・Cognito ユーザープール・API Gateway・DashboardApiLambda・MetricsAggregatorLambda |

---

## 4. 入出力概要（クロスカット観点）

| 観点 | 内容 |
|---|---|
| Connect コンタクトフロー → Lambda | イベント `event["Details"]["ContactData"]` 構造で `ContactId` / `Attributes`（customerId, userInput 等）を受領。レスポンスは Connect 属性へマップ可能なフラット JSON |
| Lambda → Bedrock | `BedrockClient` 経由。Claude（回答生成・ギャップ分析）/ Titan（埋め込み） |
| Lambda → DynamoDB | 各 Internal Module（VectorStore / HistoryRepository 等）が boto3 resource 経由でアクセス |
| EventBridge Scheduler → Lambda | 週次（日曜深夜）で CrawlerLambda / ContactLensAnalyzerLambda を起動 |
| API Gateway → DashboardApiLambda | Cognito オーソライザ経由。アクセスログ有効（SECURITY-02） |

> 全 Lambda は構造化 JSON ログを出力し、PII をログに含めない（SECURITY-03）。全ストレージは保存時暗号化・転送時 TLS 1.2 以上（SECURITY-01）。
