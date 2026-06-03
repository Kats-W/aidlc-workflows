# U-01 Core Infrastructure — Business Rules
# （インフラ設定ルール）

U-01 が全ユニットに対して強制する設定ルールを定義する。これらは Security Extension のブロッキングルールと整合する。

---

## 1. IAM 最小権限ルール

### 1.1 原則
- すべての IAM ロールは **最小権限（least privilege）** で定義する。
- ワイルドカード `Action: "*"` / `Resource: "*"` を禁止（例外は CloudTrail/Logs 等の AWS 要件のみ、その場合も Condition で絞る）。
- 各 Lambda 実行ロールには **権限境界（Permission Boundary）ポリシー** を必須付与し、権限の上限を固定する。

### 1.2 ユニット別リソースアクセス境界

| ユニット | 参照テーブル / リソース | 許可操作 |
| --- | --- | --- |
| U-02 Crawler | ContentDiff, VectorStore, S3 バケット | GetItem/PutItem/DeleteItem/Query, S3 Put/Get |
| U-03 Voice/Lex | VectorStore（読取）, CustomerHistory | Query/Scan(read), PutItem |
| U-05 CRM/History | CustomerHistory, Secrets（CRM API キー） | Query/PutItem, secretsmanager:GetSecretValue |
| U-06 自己改善 | ImprovementSuggestions, ContactAnalysis, CustomerHistory(読取) | PutItem/Query/UpdateItem |
| U-07 Dashboard | ImprovementSuggestions, ContactAnalysis（読取） | Query/Scan(read) |

- KMS: 各ロールは共用 CMK に対する `kms:Decrypt` / `kms:GenerateDataKey`（必要分のみ）を付与。
- Secrets Manager: CRM API キーへの `GetSecretValue` は U-05 のロールのみ許可。

---

## 2. KMS 暗号化必須ルール

| 対象 | ルール |
| --- | --- |
| DynamoDB 全 5 テーブル | KMS CMK による保存時暗号化必須（`encryption: CUSTOMER_MANAGED`） |
| S3 クロールコンテンツバケット | KMS CMK 暗号化（SSE-KMS）必須。`BlockPublicAccess` 全 ON |
| CloudWatch Logs 全ロググループ | KMS CMK 暗号化必須 |
| 転送時 | 全リソースで TLS 1.2 以上を強制（S3 バケットポリシーで非 TLS 拒否） |
| CMK | 自動年次ローテーション有効 |

---

## 3. DynamoDB テーブル設計ルール

| ルール | 内容 |
| --- | --- |
| キャパシティ | 全テーブル On-Demand（プロビジョンド禁止） |
| 暗号化 | KMS CMK 必須 |
| PITR | Point-In-Time Recovery 有効（信頼性 DoD） |
| GSI 上限 | 実アクセスパターンに対してのみ作成。投機的 GSI 禁止。テーブルあたり最大 2 GSI 運用とする |
| TTL | CustomerHistory のみ `expiresAt`（90 日）。差分基準/永続テーブルは TTL 設定禁止 |
| embedding 格納 | Binary（B）型で 1024 次元バイト列として格納（List<Number> 禁止） |
| PII | `text`/`transcriptSummary` 等はマスク済みのみ格納 |

---

## 4. SSM パラメータ命名規則

```
/au-jibun-bank/{env}/{service}/{resource}
```

- `{env}`: `dev` / `staging` / `prod`
- `{service}`: `dynamodb` / `kms` / `s3` / `secrets` / `connect` / `lex` / `iam` / `logs`
- `{resource}`: ケバブケースのリソース識別子

例:
```
/au-jibun-bank/dev/dynamodb/vector-store-table-name
/au-jibun-bank/dev/dynamodb/customer-history-table-name
/au-jibun-bank/dev/dynamodb/improvement-suggestions-table-name
/au-jibun-bank/dev/dynamodb/content-diff-table-name
/au-jibun-bank/dev/dynamodb/contact-analysis-table-name
/au-jibun-bank/dev/kms/cmk-arn
/au-jibun-bank/dev/s3/crawl-content-bucket-name
/au-jibun-bank/dev/secrets/crm-api-key-arn
/au-jibun-bank/dev/connect/instance-arn
/au-jibun-bank/dev/lex/bot-id
```

- パラメータ種別は `String`（ARN/ID/名前）。機微値は SSM に置かず Secrets Manager。
- CloudFormation Export は使用禁止（疎結合のため SSM 一本化）。

---

## 5. CloudWatch Logs 保持期間ルール

| ルール | 内容 |
| --- | --- |
| 保持期間 | 全 Lambda ロググループ 90 日（`retention: 90 days`） |
| 暗号化 | KMS CMK 必須 |
| ログ形式 | 構造化 JSON（AWS Lambda Powertools for Python 前提） |
| PII | CloudWatch Logs に PII を出力しない。マスク済みフィールドのみ |

---

## 6. Connect / Lex リソース命名規則

```
au-jibun-bank-{env}-{resource}
```

例:
- Connect インスタンスエイリアス: `au-jibun-bank-dev-connect`
- Lex ボット名: `au-jibun-bank-dev-bot`
- Lex ロケール: `ja-JP`（日本語）
- Lex ボットエイリアス: `au-jibun-bank-dev-bot-alias`

- U-01 は外枠（インスタンス・ボット骨格）のみ。コンタクトフロー本体・インテント詳細は命名規則に従い U-03 が追加する。

---

## 7. 全体リソース命名規則

```
au-jibun-bank-{env}-{resource}-{suffix}
```

- 例: `au-jibun-bank-dev-vector-store`、`au-jibun-bank-dev-crawl-content`（S3 はグローバル一意のためアカウント ID サフィックス付与可）。
- タグ必須: `Project=au-jibun-bank`, `Env={env}`, `Unit=U-01`, `ManagedBy=cdk`。
