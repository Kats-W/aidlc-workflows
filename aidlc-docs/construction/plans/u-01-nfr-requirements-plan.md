# U-01 Core Infrastructure — NFR Requirements Plan

# au Jibun Bank AI Agent

## 計画メタデータ

- **ユニット**: U-01 Core Infrastructure
- **フェーズ**: NFR Requirements
- **状態**: 全質問は確定済みコンテキストから解決済み。ユーザーへの追加質問なし。

---

## 実行チェックリスト

- [x] Step 1: Scalability 要件定義（DynamoDB On-Demand 自動スケール）
- [x] Step 2: Performance 要件定義（CDK デプロイ ≤5分 / DynamoDB P99 ≤10ms）
- [x] Step 3: Availability 要件定義（DynamoDB Multi-AZ / Connect SLA 99.99%）
- [x] Step 4: Security 要件定義（Security Extension 全ルール）
- [x] Step 5: Tech Stack 決定の記録（tech-stack-decisions.md）
- [x] Step 6: Reliability 要件定義（PITR / バックアップ）
- [x] Step 7: Maintainability 要件定義（IaC 全管理 / SSM 命名統一）
- [x] Step 8: Cost 要件定義（月額 ≤5,000 円 / 見積 ~3,266 円）
- [x] Step 9: nfr-requirements.md 生成
- [x] Step 10: tech-stack-decisions.md 生成

---

## Q&A セクション

### カテゴリ 1: Scalability

**Q: スケーラビリティ方式は？**
A: DynamoDB On-Demand により読み書きを自動スケール。Connect/Lex はマネージドで自動スケール。U-01 は容量計画不要。

### カテゴリ 2: Performance

**Q: パフォーマンス目標は？**
A: CDK デプロイ時間 ≤ 5 分（dev）。DynamoDB レイテンシ P99 ≤ 10ms（単一アイテム操作）。SSM パラメータ取得はキャッシュ前提。

### カテゴリ 3: Availability

**Q: 可用性目標は？**
A: DynamoDB は AWS デフォルトで Multi-AZ・リージョン冗長。Connect SLA 99.99%。U-01 リソースはステートフル基盤として高可用性を AWS マネージドに委譲。

### カテゴリ 4: Security

**Q: セキュリティ要件は？**
A: Security Extension 全ルール: 保存時 KMS CMK 暗号化、転送時 TLS 1.2 以上、IAM 最小権限 + 権限境界、PII 非ログ、CloudTrail 全 API 監査、Lambda VPC 配置（VPC は U-01 で用意、利用は U-02 以降）。

### カテゴリ 5: Tech Stack

**Q: 採用技術と理由は？**
A: CDK v2 TypeScript（型安全・L2 構築豊富）、DynamoDB On-Demand（コスト最小・自動スケール）、KMS CMK（キーポリシー制御）、SSM Parameter Store（疎結合）、Python 3.12 + uv（高速依存解決・ロックファイル）。詳細は tech-stack-decisions.md。

### カテゴリ 6: Reliability

**Q: 信頼性要件は？**
A: DynamoDB PITR 有効。CloudFormation 自動ロールバック。S3 バージョニング。KMS 自動年次ローテーション。

### カテゴリ 7: Maintainability

**Q: メンテナンス性要件は？**
A: 全リソース CDK IaC 管理。SSM パラメータ命名規則統一（`/au-jibun-bank/{env}/{service}/{resource}`）。構造化 JSON ログ（Powertools）。GitHub Actions CI（ruff + mypy + pytest + cdk synth）。
