# U-06 Self-Improvement Pipeline — Infrastructure Design

## 1. Stack

`infra/lib/stacks/improvement_stack.ts` — `ImprovementStack`（CDK v2 TypeScript、リージョン ap-northeast-1）。`infra/bin/app.ts` に `AuJibunBank-${env}-Improvement` として登録。

## 2. SharedInfraStack からの SSM 参照

| パラメータ | 用途 |
|---|---|
| `/au-jibun-bank/{env}/dynamodb/contact-analysis-table-name` | ContactAnalysis テーブル名 |
| `/au-jibun-bank/{env}/dynamodb/improvement-suggestions-table-name` | ImprovementSuggestions テーブル名 |
| `/au-jibun-bank/{env}/dynamodb/customer-history-table-name` | CustomerHistory テーブル名 |
| `/au-jibun-bank/{env}/kms/cmk-arn` | 共有 CMK |
| `/au-jibun-bank/{env}/iam/lambda-permission-boundary-arn` | Lambda permission boundary |
| `/au-jibun-bank/{env}/connect/instance-id` | Connect インスタンス ID |

## 3. Lambda 関数

| 関数 | ハンドラ | メモリ | タイムアウト |
|---|---|---|---|
| ContactLensAnalyzerLambda | `src.improvement_generator.contact_lens_analyzer.lambda_handler` | 512MB | 300s |
| GapAnalyzerLambda | `src.improvement_generator.gap_analyzer.lambda_handler` | 256MB | 120s |
| SuggestionGeneratorLambda | `src.improvement_generator.suggestion_generator.lambda_handler` | 256MB | 120s |

全関数: Python 3.12、`logRetention` 3ヶ月、`POWERTOOLS_SERVICE_NAME=u-06-improvement`。

### 環境変数

- 共通: `CONTACT_ANALYSIS_TABLE_NAME` / `IMPROVEMENT_SUGGESTIONS_TABLE_NAME` / `CUSTOMER_HISTORY_TABLE_NAME`。
- ContactLensAnalyzer: `CONNECT_INSTANCE_ID`、`GAP_ANALYZER_FUNCTION_NAME`。
- GapAnalyzer: `SUGGESTION_GENERATOR_FUNCTION_NAME`。

## 4. IAM（最小権限・`"*"` アクション不使用）

| ロール | 許可アクション | リソース |
|---|---|---|
| ContactLensAnalyzer | `dynamodb:PutItem,BatchWriteItem` / `connect:SearchContacts,ListContactAnalysis,GetContactAttributes` / `lambda:InvokeFunction` | ContactAnalysis / Connect instance(+contact) / GapAnalyzer ARN |
| GapAnalyzer | `dynamodb:Query`(ContactAnalysis) / `dynamodb:Query,GetItem`(CustomerHistory+index) / `bedrock:InvokeModel` / `lambda:InvokeFunction` | 各テーブル/index / Sonnet モデル ARN / Suggestion ARN |
| SuggestionGenerator | `dynamodb:PutItem,Query,GetItem` / `bedrock:InvokeModel` | ImprovementSuggestions(+index) / Sonnet モデル ARN |

全ロールに共有 permission boundary + `AWSLambdaBasicExecutionRole`、CMK `grantEncryptDecrypt`。

Bedrock モデル ARN: `arn:aws:bedrock:ap-northeast-1::foundation-model/anthropic.claude-sonnet-4-6-20250514-v1:0`。

## 5. EventBridge Scheduler

- `cron(0 18 ? * SUN *)`（UTC）= 月曜 03:00 JST。
- ターゲット: ContactLensAnalyzerLambda、`maximumRetryAttempts: 2`、専用 scheduler ロール（`lambda:InvokeFunction` のみ）。

## 6. Lambda 連鎖

ContactLensAnalyzer → GapAnalyzer → SuggestionGenerator を非同期 `lambda:invoke`（`InvocationType=Event`）で連鎖。Step Functions 不使用。

## 7. 監視

- CloudWatch エラーアラーム×3（しきい値1、1時間周期、`NOT_BREACHING`）。
- 出力: 3関数の関数名 `CfnOutput`。

## 8. 検証

- `npx tsc --noEmit` クリーン。
- `npx cdk synth AuJibunBank-dev-Improvement --context env=dev` 成功（`logRetention` 非推奨警告のみ、既存スタックと同様）。
