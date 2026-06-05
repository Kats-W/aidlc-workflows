# U-04 Omnichannel & Escalation — Infrastructure Design

OmnichannelStack（`infra/lib/stacks/omnichannel_stack.ts`）のリソース定義。

---

## 1. スタック概要

- **スタック名**: `AuJibunBank-<env>-Omnichannel`
- **リージョン**: `ap-northeast-1`（東京）
- **依存**: SharedInfraStack（SSM 経由でテーブル名・KMS ARN・Permission Boundary・キュー ARN を解決）

---

## 2. SSM パラメータ（読み取り）

| パラメータ | 用途 |
| --- | --- |
| `/au-jibun-bank/<env>/dynamodb/customer-history-table-name` | CustomerHistory テーブル名 |
| `/au-jibun-bank/<env>/kms/cmk-arn` | 共有 KMS CMK ARN |
| `/au-jibun-bank/<env>/iam/lambda-permission-boundary-arn` | Lambda Permission Boundary |
| `/au-jibun-bank/<env>/connect/escalation-queue-arn` | エスカレーション先 Connect キュー ARN |

---

## 3. Lambda: ChannelSwitchLambda

| 項目 | 値 |
| --- | --- |
| functionName | `au-jibun-bank-<env>-channel-switch` |
| runtime | Python 3.12 |
| handler | `src.session_manager.channel_switch.lambda_handler` |
| timeout | 10 秒 |
| memorySize | 256 MB |
| logRetention | 3 ヶ月 |
| 環境変数 | `CUSTOMER_HISTORY_TABLE_NAME`, `ESCALATION_QUEUE_ARN`, `POWERTOOLS_SERVICE_NAME=u-04-omnichannel`, `LOG_LEVEL=INFO` |

---

## 4. IAM（最小権限）

ChannelSwitchRole:

- AssumeRole: `lambda.amazonaws.com`
- Permission Boundary: 共有境界（SSM 解決）
- Managed: `AWSLambdaBasicExecutionRole`（ログのみ）
- インラインポリシー `CustomerHistorySessionReadWrite`:
  - Actions: `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:Query`
  - Resources: CustomerHistory テーブル ARN（`"*"` 不使用）
- KMS: `cmk.grantEncryptDecrypt(role)`（暗号化/復号のみ）

---

## 5. 暗号化

- CustomerHistory は SharedInfraStack で KMS CMK 暗号化済み。
- Lambda ロールに CMK の暗号化/復号権限を grant（保管時暗号化データの読み書き）。

---

## 6. エスカレーション配線（US-4.3）

- `ESCALATION_QUEUE_ARN` を SSM から解決し、Lambda env と CfnOutput（`EscalationQueueArn`）に供給。
- Connect contact flow の TransferToQueue ブロックがこの ARN を参照して有人転送。

---

## 7. 監視

- `ChannelSwitchErrorAlarm`: `metricErrors`（5 分）が 5 件以上で発報、欠損データは NOT_BREACHING。

---

## 8. 出力（CfnOutput）

| 出力 | 値 |
| --- | --- |
| `ChannelSwitchFunctionName` | Lambda 関数名 |
| `EscalationQueueArn` | エスカレーションキュー ARN（TransferToQueue 用） |

---

## 9. タグ

`Environment=<env>`, `Unit=U-04`, `Project=au-jibun-bank-ai-agent`
