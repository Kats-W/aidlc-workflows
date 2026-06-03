# U-03 AI Conversation Engine — Infrastructure Design
# （ConversationStack CDK 設計）

`infra/lib/stacks/conversation_stack.ts` の設計。U-01 SharedInfraStack の SSM エクスポートを消費し、最小権限 IAM で 4 つの Lambda を構成する。リージョンは ap-northeast-1。

---

## 1. SSM パラメータ参照（SharedInfraStack 連携）

| 参照パラメータ | 用途 |
| --- | --- |
| `/au-jibun-bank/{env}/dynamodb/vector-store-table-name` | ベクトル検索 |
| `/au-jibun-bank/{env}/dynamodb/customer-history-table-name` | 履歴/CSAT |
| `/au-jibun-bank/{env}/kms/cmk-arn` | 共用 CMK（暗号/復号） |
| `/au-jibun-bank/{env}/iam/lambda-permission-boundary-arn` | 権限境界 |

- テーブル ARN は名前から `arn:aws:dynamodb:{region}:{account}:table/{name}` で再構成（CloudFormation Export は不使用）。

---

## 2. Lambda 構成

| Lambda | ハンドラ | Runtime | Timeout | Memory |
| --- | --- | --- | --- | --- |
| RagHandlerLambda | `src.rag_handler.handler.lambda_handler` | Python 3.12 | 30s | 512MB |
| PersonalizerLambda | `src.rag_handler.personalizer.lambda_handler` | Python 3.12 | 10s | 256MB |
| EscalationLambda | `src.rag_handler.escalation.lambda_handler` | Python 3.12 | 10s | 256MB |
| CsatHandlerLambda | `src.session_manager.csat_handler.lambda_handler` | Python 3.12 | 10s | 256MB |

- RagHandler の 8 秒制約は **コード内 `asyncio.wait_for(6.0)`** で制御。Lambda の 30s は安全マージン。
- Personalizer は RagHandler から直接 invoke せず、RagHandler 内でクラスをインプロセス利用（独立 Lambda は外部呼び出し用途）。

### 環境変数（共通）

```
VECTOR_STORE_TABLE_NAME, CUSTOMER_HISTORY_TABLE_NAME,
POWERTOOLS_SERVICE_NAME=u-03-conversation-engine, LOG_LEVEL=INFO
```

EscalationLambda のみ `ESCALATION_QUEUE_ARN` を追加。

---

## 3. IAM（最小権限・`"*"` アクション禁止）

| ロール | ステートメント |
| --- | --- |
| RagRole | VectorStore: `GetItem/Query/Scan`（+index）<br>CustomerHistory: `GetItem/Query/PutItem`（+index）<br>Bedrock: `InvokeModel`（Titan v2 + Claude Sonnet 4.6 ARN 限定）<br>Comprehend: `DetectPiiEntities`（API 制約で `Resource:"*"`）<br>KMS: EncryptDecrypt |
| PersonalizerRole | CustomerHistory: `GetItem/Query`（+index）/ KMS |
| EscalationRole | 基本実行ロールのみ |
| CsatRole | CustomerHistory: `PutItem` / KMS |

- 全ロールに U-01 権限境界を付与し、AWS マネージド `AWSLambdaBasicExecutionRole` を付ける。
- KMS は `cmk.grantEncryptDecrypt(role)` で対象キーに限定。

---

## 4. 監視（CloudWatch Alarms）

| アラーム | メトリクス | 閾値 |
| --- | --- | --- |
| RagHandlerErrorAlarm | `Errors`（5 分） | ≥ 5 |
| RagHandlerThrottleAlarm | `Throttles`（5 分） | ≥ 1 |

- `treatMissingData: NOT_BREACHING`。

---

## 5. ログ・暗号化・タグ

- ログ保持: `THREE_MONTHS`（90 日、U-01 共通）。構造化 JSON（Powertools）。
- CustomerHistory は U-01 の共用 CMK で保存時暗号化。
- タグ: `Environment={env}`, `Unit=U-03`, `Project=au-jibun-bank-ai-agent`。

---

## 6. 出力（CfnOutput）

`RagHandlerFunctionName` / `PersonalizerFunctionName` / `EscalationFunctionName` / `CsatHandlerFunctionName`。
