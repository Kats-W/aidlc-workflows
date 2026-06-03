# U-01 Core Infrastructure — NFR Requirements

リージョン: ap-northeast-1（東京）。月 100 セッション未満の低トラフィック前提。

---

## 1. スケーラビリティ

| 項目 | 要件 |
| --- | --- |
| DynamoDB | On-Demand（オンデマンド）で読み書きを自動スケール。容量計画不要 |
| Connect / Lex | AWS マネージドで自動スケール |
| 想定負荷 | 月 100 セッション未満。週次バッチのスパイクにも On-Demand で追従 |

---

## 2. パフォーマンス

| 項目 | 目標 |
| --- | --- |
| CDK デプロイ時間 | ≤ 5 分（dev、SharedInfraStack 単独） |
| DynamoDB レイテンシ | P99 ≤ 10ms（単一アイテム Get/Put） |
| SSM パラメータ取得 | Consumer 側でキャッシュ（デプロイ時解決）前提 |

---

## 3. 可用性

| 項目 | 要件 |
| --- | --- |
| DynamoDB | Multi-AZ（AWS デフォルト、リージョン内 3 AZ レプリケーション） |
| Connect | SLA 99.99% |
| KMS / S3 / SSM | AWS マネージド高可用性 |

---

## 4. セキュリティ（Security Extension 全ルール）

| ルール | 要件 | 検証（DoD） |
| --- | --- | --- |
| 保存時暗号化 | KMS CMK（DynamoDB 全テーブル・S3・Logs） | テストで暗号化を検証 |
| 転送時暗号化 | TLS 1.2 以上。S3 バケットポリシーで非 TLS 拒否 | バケットポリシー検証 |
| IAM 最小権限 | 各ロールは必要リソース・操作のみ。`*` 禁止 | ポリシー検証 |
| 権限境界 | 全 Lambda ロールに Permission Boundary 付与 | ロール定義検証 |
| PII 非ログ | CloudWatch Logs に PII を出力しない | ログ設計レビュー |
| CloudTrail | 全 API コールを監査ログ記録（全リージョン） | CloudTrail 有効検証 |
| VPC | Lambda は VPC 内配置（VPC は U-01 で用意、利用は U-02 以降） | VPC 定義検証 |

**ブロッキング基準**: 上記ルール違反 0 件で U-01 完了。

---

## 5. コスト

| 項目 | 要件 |
| --- | --- |
| 月額目標 | ≤ 5,000 円（100 セッション未満） |
| 見積 | ~3,266 円 |
| DynamoDB | On-Demand（アイドルコストゼロ） |
| SSM Parameter Store | Standard（無料枠内） |
| KMS | CMK 最小コスト（鍵 1 本 + リクエスト課金） |
| 監視 | CloudWatch Billing Alarm（月 4,000 円超）/ AWS Budgets（4,000 円） |

---

## 6. 信頼性

| 項目 | 要件 |
| --- | --- |
| DynamoDB PITR | 全テーブルで Point-In-Time Recovery 有効 |
| バックアップ戦略 | PITR（35 日連続バックアップ）。週次 On-Demand バックアップは将来検討 |
| S3 | バージョニング有効（誤削除/上書き復旧） |
| デプロイ | CloudFormation 自動ロールバック |
| KMS | 自動年次キーローテーション |

---

## 7. メンテナンス性

| 項目 | 要件 |
| --- | --- |
| IaC | 全リソースを CDK v2 で管理。手動変更禁止 |
| SSM 命名規則 | `/au-jibun-bank/{env}/{service}/{resource}` で統一 |
| ログ | 構造化 JSON（AWS Lambda Powertools for Python） |
| CI/CD | GitHub Actions（ruff + mypy + pytest + cdk synth） |
| タグ統一 | Project / Env / Unit / ManagedBy タグ必須 |
