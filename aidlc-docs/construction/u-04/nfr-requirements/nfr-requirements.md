# U-04 Omnichannel & Escalation — NFR Requirements

U-04（チャネル切り替え・文脈引き継ぎ・有人エスカレーション）の非機能要件。

---

## 1. 性能 / レイテンシ

| ID | 要件 |
| --- | --- |
| NFR-4.1 | ChannelSwitchLambda はチャネル切り替え 1 回を **1 秒以内（p95）**で処理する。Lambda タイムアウトは 10 秒。 |
| NFR-4.2 | `SESSION#` の取得は単一 `GetItem`（PK+SK 完全一致）で行い、Query/Scan を回避する。 |
| NFR-4.3 | `turns` は最大 20 件に制限し、項目サイズと読み書きレイテンシを抑制する。 |

## 2. 可用性 / 耐障害性

| ID | 要件 |
| --- | --- |
| NFR-4.4 | セッション未存在（`SessionNotFoundError`）でもチャネル切り替えを失敗させず、空サマリーで継続する。 |
| NFR-4.5 | DynamoDB アクセス失敗は `DynamoAccessError` に正規化し、Connect 側でフォールバック可能にする。 |
| NFR-4.6 | エスカレーションキュー ARN は SSM から解決し、欠落時もデプロイ・実行を阻害しない配線とする。 |

## 3. セキュリティ

| ID | 要件 |
| --- | --- |
| NFR-4.7 | `SESSION#` に書き込む `text` は PII マスク済み（U-03 ルール継承）。 |
| NFR-4.8 | CustomerHistory は KMS CMK で暗号化（保管時）。Lambda には CMK の暗号化/復号権限のみ付与。 |
| NFR-4.9 | IAM は最小権限。`"*"` アクション不使用、CustomerHistory テーブル ARN にスコープ。 |
| NFR-4.10 | 認証情報・ARN のハードコード禁止。すべて SSM Parameter Store 経由で解決。 |

## 4. データ保持

| ID | 要件 |
| --- | --- |
| NFR-4.11 | `SESSION#` 項目は TTL（`expiresAt`）で 90 日後に自動失効する。 |

## 5. 観測性

| ID | 要件 |
| --- | --- |
| NFR-4.12 | aws-lambda-powertools `Logger` で構造化ログを出力（contact_id・channel_from/to・turn_count）。 |
| NFR-4.13 | CloudWatch アラームで ChannelSwitchLambda のエラー率を監視（5 分で 5 件以上で発報）。 |

## 6. 保守性 / 移植性

| ID | 要件 |
| --- | --- |
| NFR-4.14 | Python 3.12 型スタイル（`x \| None`, `list[str]`）。`pickle` 不使用。 |
| NFR-4.15 | ロジックは moto + hypothesis で単体テスト可能（外部依存はテーブル注入で差し替え）。 |
