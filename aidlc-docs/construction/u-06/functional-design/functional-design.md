# U-06 Self-Improvement Pipeline — Functional Design

## 1. Scope

U-06 は au Jibun Bank AI Agent の「自己改善サイクル」を担当する。週次で低品質コンタクトを
検出し、ナレッジギャップを分析し、わかりにくい箇所から優先的に最大10件のウェブサイト/FAQ
改善提案を生成する。担当ユーザーストーリーは次の3件。

| Story | 概要 |
|---|---|
| US-3.1 | 低品質コンタクト自動検出（CSAT≤2 / エスカレーション / NEGATIVE sentiment≥0.7） |
| US-3.2 | ナレッジギャップ分析（Claude でトピック分類・わかりにくさスコア算出） |
| US-3.3 | 週次改善提案生成（最大10件、pending重複スキップ、TTL 90日） |

## 2. Components

| Component | File | 種別 |
|---|---|---|
| ContactLensAnalyzerLambda | `src/improvement_generator/contact_lens_analyzer.py` | EventBridge トリガー Lambda |
| GapAnalyzerLambda | `src/improvement_generator/gap_analyzer.py` | Lambda-to-Lambda 連鎖 Lambda |
| SuggestionGeneratorLambda | `src/improvement_generator/suggestion_generator.py` | Lambda-to-Lambda 連鎖 Lambda |
| BedrockClient.analyze_gap / generate_suggestion | `src/common/bedrock_client.py` | 共有 Claude クライアント拡張 |

## 3. Domain Entities

### 3.1 Contact Analysis（ContactLens 由来・PII セーフ）

- Amazon Connect Contact Lens の**サマリー属性のみ**を参照（生会話テキストは扱わない）。
- 正規化形: `{contact_id, csat_score (1-5|None), escalated (bool), overall_sentiment, sentiment_confidence (float), summary_ref}`。

### 3.2 ContactAnalysis item（DynamoDB）

- PK=`weekStart`（ISO週ラベル `2026-W23`）、SK=`contactId`。
- 属性: `csatScore` / `escalated` / `overallSentiment` / `sentimentConfidence` / `summaryRef`。
- 低品質コンタクトのみ保存する。

### 3.3 Conversation Summary（CustomerHistory）

- `customerId` パーティション内の `sk = "SUMMARY#{contactId}"` アイテム。`gsi_contactId`（PK=contactId, SK=sk）で参照。
- **PII マスク済み**。Claude へはこのサマリーのみ送信する。

### 3.4 Knowledge Gap（派生）

- `{category, score, count}`。`score` = わかりにくさスコア。

### 3.5 ImprovementSuggestion item（DynamoDB）

- PK=`suggestionId`（uuid4）。GSI `gsi_status`（PK=`status`, SK=`priorityScore`）/ `gsi_week`（PK=`weekStart`）。
- 属性: `status`(`pending`/...)、`weekStart`、`targetUrl`、`improvementText`(≤200字)、`priorityScore`、`createdAt`、`ttl`(now+90日)。

## 4. Business Logic Model

### 4.1 ContactLensAnalyzerLambda.handler(event) (US-3.1)

1. `weekStart = current_week_start(now)`、`window = [now-7d, now)`。
2. `ContactLensReader.list_analyses(start, end)` でサマリー取得（指数バックオフ最大3回、`ContactLensError`）。
3. 各コンタクトを `_is_low_quality()` で判定: `csat≤2` OR `escalated` OR (`NEGATIVE` AND `confidence≥0.7`)。
4. 低品質0件 → 即 `{"analyzed": N, "low_quality": 0, "weekStart"}` を返し CloudWatch ログに記録。
5. 低品質>0件 → ContactAnalysis に `batch_writer` で保存し、GapAnalyzerLambda を非同期 invoke。
6. 戻り値: `{"analyzed": int, "low_quality": int, "weekStart": str}`。

### 4.2 GapAnalyzerLambda.handler(event) (US-3.2)

1. `event.weekStart` から ContactAnalysis を Query（低品質コンタクト群）。0件なら空で返す。
2. `escalation_rate = escalated件数 / total`。
3. 各 `contactId` の `SUMMARY#{contactId}` を `gsi_contactId` で取得（**マスク済みサマリーのみ**）。
4. `BedrockClient.analyze_gap(summaries)` でトピック分類（`BedrockThrottledError` は指数バックオフ最大3回、`ResponseParseError` は JSON 解析失敗）。
5. カテゴリごとに `confusion_score = (count/total) * escalation_rate * avg_difficulty` を算出し降順ソート。
6. SuggestionGeneratorLambda を非同期 invoke。
7. 戻り値: `{"gaps": [{"category", "score", "count"}], "count": int, "weekStart": str}`。

### 4.3 SuggestionGeneratorLambda.handler(event) (US-3.3)

1. `event.gaps` をスコア降順で最大10件選択。
2. `gsi_status` を Query して既存 `pending` の `targetUrl` 集合を取得。
3. 各 gap について `targetUrl` を導出。既存 pending と重複ならスキップ。
4. `BedrockClient.generate_suggestion(category, 200)` で改善提案テキスト（≤200字）生成。
5. `suggestionId=uuid4`、`status=pending`、`ttl=now+90日`、`weekStart`、`priorityScore`(Decimal) で条件付き PutItem（`attribute_not_exists(suggestionId)`）。
6. 戻り値: `{"generated": int}`（0〜10）。

## 5. Lambda 連鎖（Step Functions 不使用）

```
EventBridge Scheduler (cron 0 18 ? * SUN, UTC)
  → ContactLensAnalyzerLambda (Event invoke)
      → GapAnalyzerLambda (Event invoke, weekStart)
          → SuggestionGeneratorLambda (Event invoke, weekStart + gaps)
```

各段は `weekStart` を冪等キーとして独立リトライ可能。

## 6. エラーモデル

| 例外 | 発生源 | 扱い |
|---|---|---|
| `ContactLensError` | Contact Lens 読み取り失敗 | 指数バックオフ最大3回 → 枯渇で再送 |
| `BedrockThrottledError` | Bedrock レート制限 | 指数バックオフ最大3回 |
| `ResponseParseError` | Claude JSON 解析失敗 | 即時失敗（リトライ不可） |
| `DynamoAccessError` | DynamoDB 読み書き失敗 | 即時失敗 |
