# U-06 Self-Improvement Pipeline — NFR Requirements

## 1. パフォーマンス
- ContactLensAnalyzerLambda: 過去7日間のコンタクト分析を300s以内に完了。Contact Lens API 個別呼び出しは6秒未満で完了するため `asyncio.wait_for` ラップ不要。
- GapAnalyzerLambda / SuggestionGeneratorLambda: 各120s以内。Claude 呼び出しはバッチ的（週次・低頻度）。
- 週次実行・低頻度（月100件未満のコンタクト想定）であり、レイテンシ要件は緩い。

## 2. 可用性・信頼性
- 各段は `weekStart` を冪等キーとして独立リトライ可能。
- Contact Lens 読み取り / Bedrock 呼び出しは指数バックオフ最大3回。
- EventBridge Scheduler に `maximumRetryAttempts: 2`。
- 条件付き PutItem（`attribute_not_exists(suggestionId)`）でリトライ時の重複書き込みを防止。

## 3. セキュリティ・プライバシー（PII）
- **生会話テキストは一切扱わない**。ContactLens はサマリー属性のみ参照。
- Claude へ送信するのは PII マスク済みサマリー（`SUMMARY#{contactId}`）のみ、最大50件。
- IAM は最小権限（`"*"` アクション禁止）。各 Lambda は必要なテーブル/インデックス/モデル ARN のみに限定。
- DynamoDB / Lambda 環境は共有 CMK で暗号化。Lambda ロールは共有 permission boundary でバウンド。

## 4. コスト
- 週次・低頻度実行。Claude Sonnet 4.6 呼び出しはギャップ分析1回 + 提案最大10回/週。
- 月額目標（全体5,000円以内）に対し U-06 の Bedrock コストは僅少（< 数十円/月想定）。
- ベクトルストア不使用（DynamoDB ネイティブ）。

## 5. 保守性・テスト容易性
- `ContactLensReader` / `BedrockClient` を注入可能にし、moto 非対応の Connect/Bedrock API を `unittest.mock` で差し替え。
- カバレッジ目標80%以上。
- PBT（hypothesis）必須:
  - わかりにくさスコアの単調性（avg_difficulty 増 → スコア非減少、上位選択の保証）。
  - 生成数が常に 0〜10 の範囲。

## 6. 観測性
- aws-lambda-powertools `Logger` による構造化ログ。
- 全3 Lambda に CloudWatch エラーアラーム。
- 低品質0件・重複スキップ・生成件数を INFO ログに記録。

## 7. コーディング規約
- Python 3.12（`x | None`、`Optional` 不使用、`from __future__ import annotations`）。
- `async def handler(event, context)` + 同期 `lambda_handler` ラッパー。
- `pickle` 禁止。`errors.py` の `AppError` 階層を使用。
