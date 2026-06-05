# U-01 Core Infrastructure — Infrastructure Design Plan

# au Jibun Bank AI Agent

## 計画メタデータ

- **ユニット**: U-01 Core Infrastructure
- **フェーズ**: Infrastructure Design
- **CDK スタック**: `SharedInfraStack`
- **状態**: 全質問は確定済みコンテキストから解決済み。ユーザーへの追加質問なし。

---

## 実行チェックリスト

- [x] Step 1: Deployment Environment 設計（dev/staging/prod 分岐）
- [x] Step 2: Compute 設計（U-01 は Lambda なし、ロール/権限境界プレースホルダーのみ）
- [x] Step 3: Storage 設計（DynamoDB 5 テーブル / S3 / Secrets）
- [x] Step 4: Messaging 設計（U-01 スコープ外、DLQ は後続）
- [x] Step 5: Networking 設計（VPC / VPC エンドポイント枠）
- [x] Step 6: Monitoring 設計（CloudTrail / Billing Alarm / Budgets / Logs）
- [x] Step 7: Shared Infrastructure 設計（KMS / SSM / Connect / Lex）
- [x] Step 8: infrastructure-design.md 生成
- [x] Step 9: deployment-architecture.md 生成

---

## Q&A セクション

### カテゴリ 1: Deployment Environment

**Q: 環境分岐方針は？**
A: CDK Context / env 変数で `dev`/`staging`/`prod` を切替。リソース名・SSM パスに `{env}` を埋め込む。U-01 はまず dev に deploy。

### カテゴリ 2: Compute

**Q: U-01 のコンピュートは？**
A: U-01 は Lambda 実装を持たない。IAM Lambda 実行ロール + 権限境界のプレースホルダーのみ用意し後続ユニットがアタッチ。

### カテゴリ 3: Storage

**Q: ストレージ構成は？**
A: DynamoDB 5 テーブル（On-Demand, KMS, PITR）、S3 クロールコンテンツバケット（KMS, バージョニング, ライフサイクル）、Secrets Manager（CRM API キー）。

### カテゴリ 4: Messaging

**Q: メッセージング/キューは？**
A: U-01 スコープ外。DLQ・SQS は各ユニット側で定義。U-01 は SNS（Billing/Budgets 通知）のみ。

### カテゴリ 5: Networking

**Q: ネットワーク構成は？**
A: VPC + プライベートサブネット + VPC エンドポイント枠を先行整備（Lambda 利用は U-02 以降）。

### カテゴリ 6: Monitoring

**Q: 監視構成は？**
A: CloudTrail（全リージョン・S3 アーカイブ）、CloudWatch Logs（90 日・KMS）、Billing Alarm（4,000 円）、AWS Budgets（4,000 円）。

### カテゴリ 7: Shared Infrastructure

**Q: 共有基盤は？**
A: KMS CMK（共用）、SSM Parameter Store（クロススタック参照）、Connect インスタンス、Lex v2 ボット（ja-JP 外枠）。
