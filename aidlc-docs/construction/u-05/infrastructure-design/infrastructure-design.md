# U-05 SDK & Customer Profile — Infrastructure Design

CDK v2 (TypeScript) スタック `ProfileStack`（`infra/lib/stacks/profile_stack.ts`）。
リージョン ap-northeast-1（Tokyo）。`AuJibunBank-{env}-Profile` として `infra/bin/app.ts`
にインスタンス化。

## 1. SSM 入力（SharedInfraStack エクスポート）

`base = /au-jibun-bank/${env}` として解決:

| パラメータ | 用途 |
|---|---|
| `${base}/dynamodb/customer-history-table-name` | プロファイル参照テーブル名 |
| `${base}/secrets/crm-api-key-arn` | CRM API キーのシークレット ARN |
| `${base}/kms/cmk-arn` | SQS 暗号化・シークレット復号の共有 CMK |
| `${base}/iam/lambda-permission-boundary-arn` | Lambda ロールの permission boundary |

CustomerHistory の ARN はテーブル名から再構成:
`arn:aws:dynamodb:{region}:{account}:table/{name}`。

## 2. リソース

### 2.1 CustomerProfileLambda

- Runtime Python 3.12 / memory 256MB / timeout **10s**。
- handler `src.profile.handler.lambda_handler`。
- env: `CUSTOMER_HISTORY_TABLE_NAME`, `POWERTOOLS_SERVICE_NAME=u-05-customer-profile`,
  `LOG_LEVEL=INFO`。
- ログ保持 3 ヶ月。

### 2.2 CrmWriterLambda

- Runtime Python 3.12 / memory 256MB / timeout **30s**。
- handler `src.profile.crm_writer.lambda_handler`。
- イベントソース: `CrmWriteQueue`（`SqsEventSource`, batchSize 5）。
- env: `CRM_ENDPOINT`, `CRM_API_KEY_ARN`, `CRM_DLQ_URL`,
  `POWERTOOLS_SERVICE_NAME=u-05-crm-writer`, `LOG_LEVEL=INFO`。

### 2.3 SQS

- **CrmWriteQueue**: KMS（共有 CMK）暗号化 / `enforceSSL` / 保持 4 日 /
  visibilityTimeout 180s（= CrmWriter タイムアウトの 6 倍）/ redrive: DLQ, maxReceiveCount 3。
- **CrmWriteDlq**: KMS 暗号化 / `enforceSSL` / **保持 14 日**。アプリ側 `_send_to_dlq` の
  明示送信先かつ SQS redrive 先を兼ねる。

### 2.4 CloudWatch アラーム

- `CustomerProfileErrorAlarm`: `Errors >= 5`（5 分）。
- `CrmWriterErrorAlarm`: `Errors >= 3`（5 分）。
- いずれも `treatMissingData = NOT_BREACHING`。

## 3. IAM（最小権限・`"*"` アクション無し）

### CustomerProfileRole

- `dynamodb:GetItem`, `dynamodb:Query` on CustomerHistory（テーブル + `index/*`）。
- 共有 CMK `grantEncryptDecrypt`。
- AWSLambdaBasicExecutionRole（Logs）+ permission boundary。

### CrmWriterRole

- `sqs:ReceiveMessage|DeleteMessage|GetQueueAttributes` on CrmWriteQueue。
- `sqs:SendMessage` on CrmWriteDlq。
- `secretsmanager:GetSecretValue` on CRM API キー ARN。
- 共有 CMK `grantEncryptDecrypt`。
- AWSLambdaBasicExecutionRole（Logs）+ permission boundary。

## 4. デプロイ統合

`infra/bin/app.ts`:

```ts
new ProfileStack(app, `AuJibunBank-${env}-Profile`, {
  env: cdkEnv, envName: env,
  crmEndpoint: app.node.tryGetContext('crmEndpoint') ?? 'https://crm.jibunbank.example/api/v1/summaries',
});
```

`crmEndpoint` は CDK context（`--context crmEndpoint=...`）で上書き可能。

## 5. データフロー

```
[Native App SDK / Connect] --auId--> CustomerProfileLambda
        |                                   |
        | hash -> customerId                v
        |                          CustomerHistory (gsi-customer-id, sk=PROFILE)
        |                                   |
        |                                   v  {customer_id, tier, found}
   ...contact...                         Connect 属性
        |
   contact end -> summary -> (DynamoDB Streams/EventBridge Pipe) -> CrmWriteQueue
                                                                        |
                                                                        v
                                                                  CrmWriterLambda
                                                                   |        |
                                                          (httpx POST)   anonymous->skip
                                                                   |        terminal/4xx
                                                                   v        v
                                                                CRM API   CrmWriteDlq
```

## 6. タグ

`Environment={env}`, `Unit=U-05`, `Project=au-jibun-bank-ai-agent`。

## 7. 検証結果

- `npx tsc --noEmit` クリーン。
- `npx cdk synth AuJibunBank-dev-Profile` 正常合成（2 Lambda / 2 SQS / 2 Alarm / IAM）。
