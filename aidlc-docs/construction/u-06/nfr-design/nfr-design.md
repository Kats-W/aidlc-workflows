# U-06 Self-Improvement Pipeline — NFR Design

## 1. パフォーマンス設計

- ブロッキング boto3 呼び出しは `asyncio.to_thread` でオフロードし、async ハンドラのイベントループを塞がない。
- ContactAnalysis 保存は `batch_writer` でまとめ書き。
- Claude へ送るサマリーは最大50件にクランプ（`summaries[:50]`）してトークン/レイテンシを上限化。
- Lambda サイズ: ContactLensAnalyzer 512MB/300s、Gap/Suggestion 256MB/120s。

## 2. 信頼性設計

- 指数バックオフ: ContactLens `1s→2s`（最大3回）、Bedrock `1s→2s`（最大3回）。
- 冪等性: `weekStart`（ISO週）をパイプライン全段のキーに採用。SuggestionGenerator は条件付き PutItem で重複生成を防止。
- 重複スキップ: `gsi_status` で `status="pending"` の `targetUrl` を取得し、同一 URL の再生成を抑止。

## 3. セキュリティ設計

- PII 境界: ContactLens は `_summarise()` でサマリー属性のみ射影。Gap 分析は `SUMMARY#` アイテムのみ取得。生テキストへのパスは存在しない。
- IAM スコープ:
  - ContactLensAnalyzer: ContactAnalysis 書き込み / Connect 読み取り / GapAnalyzer invoke。
  - GapAnalyzer: ContactAnalysis Query / CustomerHistory(+index) Query / Bedrock InvokeModel(Sonnet) / Suggestion invoke。
  - SuggestionGenerator: ImprovementSuggestions(+index) 読み書き / Bedrock InvokeModel(Sonnet)。
- Bedrock は Sonnet 4.6 モデル ARN のみに限定。共有 CMK で暗号化/復号。

## 4. テスト設計

- moto: DynamoDB（ContactAnalysis / CustomerHistory + gsi_contactId / ImprovementSuggestions + gsi_status/gsi_week）。
- `unittest.mock`: Connect `search_contacts`、Bedrock `analyze_gap`/`generate_suggestion`。
- PBT: `confusion_score` の avg_difficulty 単調性、ソート後スコア非増、生成数 0〜10。
- カバレッジ実績91%（目標80%超）。

## 5. 観測性設計

- 構造化ログキー: `weekStart` / `analyzed` / `low_quality` / `gaps` / `generated` / `attempt` / `code`。
- CloudWatch エラーアラーム（3 Lambda、1時間周期、しきい値1）。
