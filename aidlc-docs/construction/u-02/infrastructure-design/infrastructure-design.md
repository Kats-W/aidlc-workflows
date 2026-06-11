# U-02 Infrastructure Design — KnowledgePipelineStack

CDK v2 (TypeScript) スタック `infra/lib/stacks/knowledge_pipeline_stack.ts`。

---

## 1. SharedInfra 参照（SSM）

`ssm.StringParameter.valueForStringParameter` で以下を解決:

| パラメータ | 用途 |
| --- | --- |
| `/au-jibun-bank/{env}/dynamodb/vector-store-table-name` | VectorStore テーブル名 |
| `/au-jibun-bank/{env}/dynamodb/content-diff-table-name` | ContentDiff テーブル名 |
| `/au-jibun-bank/{env}/s3/crawl-content-bucket-name` | クロール S3 バケット名 |
| `/au-jibun-bank/{env}/kms/cmk-arn` | 共有 CMK ARN |
| `/au-jibun-bank/{env}/iam/lambda-permission-boundary-arn` | 権限境界 ARN |

テーブル名/バケット名から ARN を再構築（`arn:aws:dynamodb:{region}:{account}:table/{name}` 等）。

---

## 2. Lambda 関数

| 関数 | runtime | memory | timeout | handler |
| --- | --- | --- | --- | --- |
| `CrawlerLambda` | Python 3.12 | 1024 MB | 15 分 | `src.crawler.handler.lambda_handler` |
| `EmbedderLambda` | Python 3.12 | 512 MB | 10 分 | `src.vector_store.handler.lambda_handler` |

環境変数:

- 共通: `VECTOR_STORE_TABLE_NAME`, `CONTENT_DIFF_TABLE_NAME`, `CRAWL_CONTENT_BUCKET`, `POWERTOOLS_SERVICE_NAME`, `LOG_LEVEL`
- Crawler 追加: `CRAWLER_TARGET_URLS`(JSON), `EMBEDDER_FUNCTION_NAME`

ログ保持: 90 日（`THREE_MONTHS`）。

---

## 3. EventBridge Scheduler

```
name: au-jibun-bank-{env}-weekly-crawl
scheduleExpression: cron(0 17 ? * SAT *)   # Sat 17:00 UTC = Sun 02:00 JST
flexibleTimeWindow: OFF
target: CrawlerLambda (retry max 2)
```

専用 Scheduler ロールが `lambda:InvokeFunction` を Crawler に対してのみ許可。

---

## 4. IAM（最小権限・"*" 排除）

### CrawlerRole

| Sid | Action | Resource |
| --- | --- | --- |
| ContentDiffReadWrite | GetItem/PutItem/BatchWriteItem/DeleteItem/Scan/Query | ContentDiff テーブル + index |
| CrawlBucketWrite | s3:PutObject/GetObject/DeleteObject | `{bucket}/*` |
| InvokeEmbedder | lambda:InvokeFunction | EmbedderLambda ARN |
| (KMS) | Encrypt/Decrypt（grantEncryptDecrypt） | 共有 CMK |

### EmbedderRole

| Sid | Action | Resource |
| --- | --- | --- |
| VectorStoreReadWrite | GetItem/PutItem/DeleteItem/Scan/Query | VectorStore テーブル + index |
| CrawlBucketRead | s3:GetObject | `{bucket}/*` |
| BedrockEmbed | bedrock:InvokeModel | `foundation-model/amazon.titan-embed-text-v2:0` のみ |
| (KMS) | Encrypt/Decrypt | 共有 CMK |

- 両ロールに SharedInfra の権限境界（permission boundary）を適用。
- `AWSLambdaBasicExecutionRole`（Logs）のみマネージドポリシーとして付与。
- Bedrock は単一 modelId に限定し `"*"` を排除。

---

## 5. CloudWatch アラーム

| アラーム | メトリクス | 閾値 |
| --- | --- | --- |
| `{prefix}-crawler-errors` | Crawler Errors (1h) | ≥ 1 |
| `{prefix}-embedder-errors` | Embedder Errors (5m) | ≥ 3 |

`treatMissingData: NOT_BREACHING`。

---

## 6. タグ / 出力

- タグ: `Environment`, `Unit=U-02`, `Project`。
- 出力: `CrawlerFunctionName`, `EmbedderFunctionName`。
