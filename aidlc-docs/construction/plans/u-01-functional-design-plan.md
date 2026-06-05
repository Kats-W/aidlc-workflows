# U-01 Core Infrastructure — Functional Design Plan

# au Jibun Bank AI Agent

## 計画メタデータ

- **ユニット**: U-01 Core Infrastructure
- **フェーズ**: Functional Design
- **性質**: 純粋インフラユニット（アプリケーションのビジネスロジックなし）。本フェーズの「ビジネスロジック」はインフラリソース設計判断ロジックとして扱う。
- **CDK スタック**: `SharedInfraStack`（`infra/stacks/shared_infra_stack.py` 相当 / 実体は CDK v2 TypeScript）
- **状態**: 全質問は確定済みコンテキストから解決済み。ユーザーへの追加質問なし。

---

## 実行チェックリスト

### Part 1: スコープ確認

- [x] Step 1: U-01 の責務境界確認（共有基盤リソースの一括定義）
- [x] Step 2: 担当コンポーネント棚卸し（DynamoDB 5 テーブル / IAM / Secrets / KMS / Logs / Connect / Lex / S3 / AppError）
- [x] Step 3: スコープ外項目の明示（Lambda 実装・Connect フロー本体・Lex インテント詳細）

### Part 2: 機能設計ドキュメント生成

- [x] Step 4: business-logic-model.md 生成（インフラ設計判断ロジック）
- [x] Step 5: business-rules.md 生成（IAM/KMS/DynamoDB/SSM/Logs ルール）
- [x] Step 6: domain-entities.md 生成（テーブル・バケット・キー・シークレット・SSM パラメータ・Connect/Lex）

### Part 3: 完了

- [x] Step 7: DoD 整合性チェック（5 テーブル・暗号化・Connect/Lex・AppError）
- [x] Step 8: 後続ユニットへの参照インターフェース（SSM パラメータ名）確定

---

## Q&A セクション

### カテゴリ 1: Business Logic Modeling（DynamoDB テーブル設計ロジック）

**Q1-1: U-01 はアプリケーションのビジネスロジックを持つか？**
A: 持たない。U-01 は純粋インフラユニット。本ドキュメントにおける「ビジネスロジック」はインフラリソースの設計判断ロジック（テーブル設計理由・SSM 参照パターン・KMS 共用設計）を指す。

**Q1-2: DynamoDB のテーブル分割方針は？**
A: ドメイン境界ごとに 5 テーブルに分割する（VectorStore / CustomerHistory / ImprovementSuggestions / ContentDiff / ContactAnalysis）。CustomerHistory と ContactAnalysis は複数アイテム種別を PK+SK に束ねる Single-Table 的設計、その他はシンプルキー。理由は各ドメインのライフサイクル・TTL・アクセスパターンが異なるため。

**Q1-3: キャパシティモードは？**
A: 全テーブル On-Demand（オンデマンド）。月 100 セッション未満の低トラフィックでプロビジョニング不要、コスト最小化・自動スケールのため。

**Q1-4: GSI 設計の根拠は？**
A: 各テーブルの二次アクセスパターン（URL 単位削除・コンタクト単位参照・ステータス別・週単位）に対し必要最小限の GSI のみ作成。ContactAnalysis は PK(weekStart)+SK(contactId) で参照充足するため GSI なし。

### カテゴリ 2: Domain Model（エンティティとその関係）

**Q2-1: U-01 が定義するエンティティは？**
A: インフラエンティティ＝ DynamoDB テーブル 5 種、S3 バケット 1 種、KMS CMK 1 種、Secrets Manager シークレット 1 種、CloudWatch Logs ロググループ群、Connect インスタンス、Lex v2 ボット、IAM ロール/権限境界、SSM パラメータ群、AppError 例外階層。

**Q2-2: エンティティ間の関係は？**
A: KMS CMK が DynamoDB 全テーブル・S3・Logs を暗号化（1:N）。SSM パラメータが各リソースの ARN/ID を保持し後続スタックが参照（疎結合）。Secrets Manager シークレットは U-05 が参照。

### カテゴリ 3: Business Rules（インフラ設定ルール）

**Q3-1: 暗号化ルールは？**
A: 全 DynamoDB テーブル・S3 バケット・CloudWatch Logs は KMS CMK で保存時暗号化必須。転送時は TLS 1.2 以上必須。

**Q3-2: IAM ルールは？**
A: 最小権限。各ユニットは自分が必要とするテーブル・操作のみに限定。権限境界（Permission Boundary）ポリシーで上限を固定。

**Q3-3: ログ保持・PII ルールは？**
A: CloudWatch Logs 保持期間は 90 日。PII は CloudWatch Logs に出力しない（マスク済みのみ）。構造化 JSON ログ前提。

### カテゴリ 4: Data Flow（CDK 出力 → SSM → 後続スタック）

**Q4-1: スタック間でリソース ARN/ID をどう共有するか？**
A: CloudFormation Export は使わず、SSM Parameter Store に格納。命名規則 `/au-jibun-bank/{env}/{service}/{resource}`。後続スタックは SSM からの値参照のみで構築する。

**Q4-2: 参照の方向は？**
A: `SharedInfraStack`（U-01）が全パラメータを書き込み（Producer）。U-02〜U-07 が読み取り（Consumer）。U-01 はどのユニットにも依存しない最上流。

### カテゴリ 5: Integration Points（Connect / Lex 統合方法）

**Q5-1: Connect/Lex はどう IaC 管理するか？**
A: CDK L1 Construct（aws-connect / aws-lex）でインスタンス・ボットの外枠のみ定義。GUI で初期設定後 JSON export → リポジトリ管理 → import するフロー。インテント詳細は U-03 が担当。

**Q5-2: U-01 が提供する Connect/Lex のインターフェースは？**
A: Connect インスタンス ARN/ID、Lex ボット ID/エイリアス ARN を SSM に Export。後続ユニットは ARN/ID 参照のみ。

### カテゴリ 6: Error Handling（CDK デプロイ失敗時の対処）

**Q6-1: CDK デプロイ失敗時の挙動は？**
A: CloudFormation の自動ロールバックに委ねる。手動 `cdk destroy` は運用上は使用しない。`cdk diff` で事前差分確認、CI/CD は `cdk deploy --require-approval never`。

**Q6-2: AppError 例外階層の位置づけは？**
A: `src/common/` に基底クラス `AppError` と派生例外を定義。U-01 では型定義と単体テストのみ（実利用は後続ユニット）。リトライ対象（`BedrockThrottledError`）・DLQ 行き（`CrmApiError`）・フォールバック（`TimeoutBudgetExceeded`）の分類を型で表現。
