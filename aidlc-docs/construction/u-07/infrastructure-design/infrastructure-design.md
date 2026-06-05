# U-07 Admin Dashboard — Infrastructure Design

## 1. Stack: DashboardStack（`infra/lib/stacks/dashboard_stack.ts`）

```
DashboardStack (AuJibunBank-<env>-Dashboard)
├── SSM lookups (from SharedInfraStack)
│   ├── /au-jibun-bank/<env>/dynamodb/improvement-suggestions-table-name
│   ├── /au-jibun-bank/<env>/dynamodb/customer-history-table-name
│   ├── /au-jibun-bank/<env>/dynamodb/contact-analysis-table-name
│   ├── /au-jibun-bank/<env>/kms/cmk-arn
│   └── /au-jibun-bank/<env>/iam/lambda-permission-boundary-arn
├── Cognito UserPool (MFA OPTIONAL TOTP, password policy, advanced security)
├── Cognito UserPoolClient (SPA, no secret)
├── MetricsAggregatorLambda (Python 3.12, 256MB, 60s)
├── DashboardApiLambda (Python 3.12, 256MB, 30s) — invokes aggregator
├── HTTP API (apigatewayv2) + Cognito JWT authorizer + Lambda proxy routes
├── EventBridge Scheduler (weekly Sun 18:30 UTC -> MetricsAggregator)
├── Amplify CfnApp (config only; manual/CI deploy)
├── CloudWatch error alarms (both Lambdas)
└── Outputs / SSM params: userPoolId, userPoolClientId, apiEndpoint, amplifyAppId
```

## 2. リソース詳細

### 2.1 Cognito

- `UserPool`: `selfSignUpEnabled=false`、`mfa=OPTIONAL`、`mfaSecondFactor={otp:true}`、
  `passwordPolicy`（minLength8, requireUppercase/Lowercase/Digits/Symbols）、
  `advancedSecurityMode=ENFORCED`、`signInAliases={email}`。
- `UserPoolClient`: `generateSecret=false`、`authFlows={userSrp:true}`、`accessTokenValidity`/
  `idTokenValidity`=60min、`refreshTokenValidity`=30d。

### 2.2 Lambda

| Lambda | handler | memory | timeout |
|---|---|---|---|
| MetricsAggregatorLambda | `src.dashboard_api.metrics_aggregator.lambda_handler` | 256MB | 60s |
| DashboardApiLambda | `src.dashboard_api.handler.lambda_handler` | 256MB | 30s |

- 共有 permission boundary を付与。コードは `lambda.Code.fromAsset('..')`（infra/tests/docs を除外）。
- DashboardApiLambda 環境変数: テーブル名、`METRICS_AGGREGATOR_FUNCTION_NAME`。

### 2.3 IAM（最小権限）

- DashboardApiLambda:
  - `dynamodb:Query`/`GetItem`/`UpdateItem` on ImprovementSuggestions（+ `/index/*`）。
  - `lambda:InvokeFunction` on MetricsAggregator ARN。
  - CMK `grantEncryptDecrypt`。
- MetricsAggregatorLambda:
  - `dynamodb:Query`/`Scan`/`GetItem` on CustomerHistory（+ `/index/*`）。
  - CMK `grantEncryptDecrypt`。

### 2.4 API Gateway HTTP API

- `apigatewayv2.HttpApi` + `HttpJwtAuthorizer`（issuer=UserPool、audience=Client ID）。
- ルート（全て `HttpLambdaIntegration` → DashboardApiLambda、authorizer 付き）:
  - `GET /suggestions`、`PATCH /suggestions/{id}`、`GET /suggestions/csv`、`GET /metrics`。
- CORS: Amplify ドメインからの `GET`/`PATCH`、`Authorization` ヘッダー許可。

### 2.5 EventBridge Scheduler

- `cron(30 18 ? * SUN *)` UTC（日曜 18:30 UTC）→ MetricsAggregatorLambda を invoke（30d 既定）。

### 2.6 Amplify

- `aws_amplify.CfnApp`（L1）で設定のみ作成（リポジトリ接続なし）。実ビルド/デプロイは CI/CD。
- App 名: `au-jibun-bank-<env>-dashboard`。`amplifyAppId` を SSM 出力。

### 2.7 出力

- `CfnOutput` + SSM `StringParameter`:
  - `/au-jibun-bank/<env>/dashboard/user-pool-id`
  - `/au-jibun-bank/<env>/dashboard/user-pool-client-id`
  - `/au-jibun-bank/<env>/dashboard/api-endpoint`
  - `/au-jibun-bank/<env>/dashboard/amplify-app-id`

## 3. app.ts への登録

`infra/bin/app.ts` に `DashboardStack`（`AuJibunBank-<env>-Dashboard`）を追加。U-07 プレースホルダー
コメントを置き換える。

## 4. デプロイ

```bash
(cd infra && npx cdk synth AuJibunBank-dev-Dashboard --context env=dev)
```

- frontend は本ユニットでは実ビルドしない（コード生成のみ）。
