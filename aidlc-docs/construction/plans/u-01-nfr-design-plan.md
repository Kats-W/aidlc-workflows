# U-01 Core Infrastructure — NFR Design Plan

# au Jibun Bank AI Agent

## 計画メタデータ

- **ユニット**: U-01 Core Infrastructure
- **フェーズ**: NFR Design
- **状態**: 全質問は確定済みコンテキストから解決済み。ユーザーへの追加質問なし。

---

## 実行チェックリスト

- [x] Step 1: Resilience パターン設計（CloudFormation 自動ロールバック / PITR）
- [x] Step 2: Scalability パターン設計（On-Demand）
- [x] Step 3: Performance パターン設計（SSM キャッシュ / 構造化ログ）
- [x] Step 4: Security パターン設計（Permission Boundary / KMS ローテーション / Secrets ローテーション将来）
- [x] Step 5: Logical Components 特定（Billing Alarm / CloudTrail / Budgets / VPC / CMK / Secrets / SSM）
- [x] Step 6: 可観測性パターン設計（Powertools / カスタムメトリクス名前空間）
- [x] Step 7: コスト管理パターン設計（Billing Alarm 4,000 円）
- [x] Step 8: nfr-design-patterns.md 生成
- [x] Step 9: logical-components.md 生成

---

## Q&A セクション

### カテゴリ 1: Resilience

**Q: 障害・デプロイ失敗時の回復設計は？**
A: CloudFormation 自動ロールバック。DynamoDB PITR（35 日）。S3 バージョニング。手動 destroy は使用しない。

### カテゴリ 2: Scalability

**Q: スケール設計パターンは？**
A: DynamoDB On-Demand に委譲。U-01 自体はスケールロジックを持たない（マネージドに委譲）。

### カテゴリ 3: Performance

**Q: パフォーマンス設計パターンは？**
A: SSM パラメータは Consumer 側でデプロイ時解決/キャッシュ。構造化ログは非同期出力で関数性能に影響させない。

### カテゴリ 4: Security

**Q: セキュリティ設計パターンは？**
A: IAM Permission Boundary、KMS CMK 自動年次ローテーション、Secrets Manager 自動ローテーション（将来）、CloudTrail 全 API、S3 非 TLS 拒否ポリシー。

### カテゴリ 5: Logical Components

**Q: U-01 が論理的に持つ運用コンポーネントは？**
A: CloudWatch Billing Alarm、CloudTrail、AWS Budgets、VPC（後続用）、KMS CMK、Secrets Manager、SSM Parameter Store 設計。詳細は logical-components.md。
