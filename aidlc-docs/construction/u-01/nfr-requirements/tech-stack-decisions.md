# U-01 Core Infrastructure — Tech Stack Decisions

技術選定の判断記録（ADR 形式）。

---

## ADR-01: CDK v2 TypeScript を IaC に採用

- **決定**: インフラは AWS CDK v2（TypeScript）で記述する。
- **理由**:
  - 型安全。コンパイル時にリソースプロパティの誤りを検出。
  - L2 Construct が豊富で DynamoDB/S3/KMS/Logs を簡潔に定義できる。
  - `cdk diff` / `cdk synth` で CI に組み込みやすい。
- **代替案**: Terraform（マルチクラウドだが AWS L2 相当の抽象が薄い）、CloudFormation 生 YAML（冗長）。
- **備考**: Lambda 実装は Python 3.12。IaC のみ TypeScript。

---

## ADR-02: DynamoDB On-Demand キャパシティ

- **決定**: 全 5 テーブルを On-Demand モードにする。
- **理由**:
  - 月 100 セッション未満の低負荷でプロビジョンド容量はアイドルコストになる。
  - On-Demand はリクエスト課金でアイドルコストゼロ、週次バッチのスパイクにも自動追従。
  - 月額コスト目標 ≤ 5,000 円（見積 ~3,266 円）に最適。
- **代替案**: Provisioned + Auto Scaling（低負荷では割高・運用複雑）。

---

## ADR-03: KMS CMK（Customer Managed Key）を採用

- **決定**: AWS Managed Key ではなく CMK を 1 本作成し、DynamoDB・S3・Logs で共用。
- **理由**:
  - キーポリシーで「誰が暗号化/復号できるか」を明示制御でき、IAM 最小権限・監査と整合。
  - 自動年次ローテーション・キー削除制御が可能。
  - 将来の機微度別キー分割の余地を残す。
- **代替案**: AWS Managed Key（キーポリシー制御不可・監査粒度が粗い）。
- **トレードオフ**: CMK は月額 + リクエスト課金が発生するが最小コスト。

---

## ADR-04: SSM Parameter Store でクロススタック参照

- **決定**: CloudFormation Export ではなく SSM Parameter Store で ARN/ID を共有。
- **理由**:
  - Export は参照中スタックがあると Producer を変更/削除できず硬直する。
  - SSM は疎結合で、U-01 を独立してデプロイ・更新できる。
  - 階層パスで命名体系化できる（`/au-jibun-bank/{env}/{service}/{resource}`）。
- **代替案**: CloudFormation Export（強い依存ロック）。
- **備考**: 機微値は SSM に置かず Secrets Manager を使う。

---

## ADR-05: Python 3.12 + uv

- **決定**: Lambda ランタイムは Python 3.12、依存管理は uv + pyproject.toml。
- **理由**:
  - uv は依存解決が高速で、ロックファイルにより再現性を担保。
  - pyproject.toml に ruff/mypy/pytest 設定を集約できる。
  - Python 3.12 は Lambda サポート + 性能改善。
- **代替案**: pip + requirements.txt（解決遅く再現性低）、Poetry（uv より低速）。

---

## ADR-06: 構造化ログに AWS Lambda Powertools for Python

- **決定**: 全 Lambda の構造化 JSON ログに Powertools を採用（U-01 はログ基盤側を用意）。
- **理由**:
  - JSON ログ・相関 ID・メトリクス・トレースを標準化。
  - PII 非ログ要件と整合（フィールド制御が容易）。
- **代替案**: 素の logging（構造化・相関 ID を自前実装する必要）。
