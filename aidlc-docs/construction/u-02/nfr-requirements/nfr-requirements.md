# U-02 NFR Requirements — Knowledge Pipeline

---

## 1. パフォーマンス

| ID | 要件 | 目標 |
| --- | --- | --- |
| PERF-1 | RAG 検索全体のレイテンシ予算 | **8 秒以内**（U-03 チャット応答制約から逆算） |
| PERF-2 | ベクトル検索（キャッシュ温時） | 200 ms 以内 |
| PERF-3 | ベクトル検索（コールドスタート・全件スキャン） | 2 秒以内 |
| PERF-4 | CrawlerLambda 実行時間 | 15 分タイムアウト内（polite delay 込み） |
| PERF-5 | EmbedderLambda 1 チャンク埋め込み | Bedrock 往復 1 秒以内 |

- 検索の高速化のため、コーパス全体を numpy 行列として /tmp に 15 分キャッシュし、ウォームスタート間で DynamoDB 全件スキャンを回避する。

---

## 2. コスト

| ID | 要件 |
| --- | --- |
| COST-1 | 埋め込みは **差分チャンクのみ** 実行（変更が無ければ Bedrock を呼ばない）。 |
| COST-2 | DynamoDB は On-Demand（PAY_PER_REQUEST）。週次バーストに最適。 |
| COST-3 | EmbedderLambda メモリ 1024MB、CrawlerLambda 1024MB（過剰割当を避ける）。EmbedderLambda は当初 512MB だったが、最終バッチのキャッシュ再構築（コーパス全体を numpy 行列化, ピーク約 300MB+）で余裕が薄く、CPU 増による高速化とタイムアウトリトライ回避でコスト中立のため 1024MB に引き上げた。 |
| COST-4 | クロールは週次 1 回（Sun 02:00 JST）に限定。 |
| COST-5 | Titan Embeddings v2、`dimensions=1024`（精度とストレージのバランス）。 |

---

## 3. セキュリティ

| ID | 要件 |
| --- | --- |
| SEC-1 | **pickle 使用禁止**。/tmp キャッシュは numpy `.npy` + JSON。 |
| SEC-2 | 同期 `requests` 禁止、`httpx`(async) を使用。 |
| SEC-3 | IAM は最小権限。`"*"` アクション禁止（Bedrock は単一 modelId に限定）。 |
| SEC-4 | 認証情報のハードコード禁止。設定は環境変数 / SSM 経由。 |
| SEC-5 | DynamoDB / S3 は SharedInfra の KMS CMK で暗号化。 |
| SEC-6 | クロールは robots.txt を遵守し、識別可能な User-Agent を送信。 |

---

## 4. 信頼性

| ID | 要件 |
| --- | --- |
| REL-1 | Bedrock スロットリングは `BedrockThrottledError`(retryable) で表現し指数バックオフ対象とする。 |
| REL-2 | HTTP 取得失敗は `FetchTimeoutError`(retryable)。 |
| REL-3 | 単一 URL の失敗はクロール全体を停止させず `errors[]` に記録。 |
| REL-4 | DynamoDB / S3 障害は型付き例外（`DynamoAccessError` / `S3AccessError`）に変換。 |
| REL-5 | Scheduler のリトライは最大 2 回。 |
| REL-6 | Lambda エラーは CloudWatch アラームで検知。 |

---

## 5. 可観測性

| ID | 要件 |
| --- | --- |
| OBS-1 | `aws_lambda_powertools.Logger` による構造化 JSON ログ。 |
| OBS-2 | CrawlerLambda 戻り値に crawled / added / changed / deleted / errors を含める。 |
| OBS-3 | CloudWatch アラーム（Crawler / Embedder のエラー）。 |
