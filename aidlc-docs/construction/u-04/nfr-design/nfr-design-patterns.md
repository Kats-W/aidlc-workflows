# U-04 Omnichannel & Escalation — NFR Design Patterns

U-04 で適用する設計パターンと、対応する NFR・根拠。

---

## DP-4.1 単一テーブル + 完全一致 GetItem

- **パターン**: `SESSION#<contactId>` を PK=`customerId`+SK で一意化し、`GetItem` で取得。
- **対応 NFR**: NFR-4.1, NFR-4.2（低レイテンシ）。
- **根拠**: Query/Scan を避けることで p95 レイテンシと RCU を最小化。

## DP-4.2 リングバッファ的ターン保持（最新 N）

- **パターン**: 追記時に既存 `turns` を読み、末尾追加後 `[-MAX_TURNS:]` で切り詰めて put。
- **対応 NFR**: NFR-4.3, NFR-4.11。
- **トレードオフ**: read-modify-write の 2 アクセス。低頻度のチャネル切り替えでは許容。

## DP-4.3 グレースフルフォールバック（新規セッション）

- **パターン**: `handler` が `SessionNotFoundError` を捕捉し、空サマリー・turn_count=0 で正常応答。
- **対応 NFR**: NFR-4.4。
- **根拠**: チャネル切り替えを例外で失敗させず、顧客体験を維持。

## DP-4.4 エラー正規化（ドメイン例外）

- **パターン**: boto3 `ClientError` を `DynamoAccessError` に、入力不備を `ValidationError` に変換。
- **対応 NFR**: NFR-4.5, NFR-4.6。
- **根拠**: 共通 `AppError` 階層により呼び出し側のリトライ/フォールバック判断を型で駆動。

## DP-4.5 依存性注入（テーブル注入）

- **パターン**: `SessionContextManager(table=...)` でテーブルを注入可能にし、本番は env 名から解決。
- **対応 NFR**: NFR-4.15。
- **根拠**: moto による単体テストと hypothesis PBT を容易化。

## DP-4.6 最小権限 IAM + Permission Boundary

- **パターン**: CustomerHistory テーブル ARN にスコープした `GetItem/PutItem/Query` のみ付与。
  全ロールに共有 Permission Boundary を適用。
- **対応 NFR**: NFR-4.8, NFR-4.9。
- **根拠**: `"*"` アクション禁止、横展開リスクの抑制。

## DP-4.7 設定外部化（SSM Parameter Store）

- **パターン**: テーブル名・KMS ARN・Permission Boundary ARN・エスカレーションキュー ARN を SSM から解決。
- **対応 NFR**: NFR-4.10。
- **根拠**: ハードコード禁止、スタック間疎結合。

## DP-4.8 構造化ログ + アラーム

- **パターン**: powertools `Logger` + CloudWatch Alarm（エラー率）。
- **対応 NFR**: NFR-4.12, NFR-4.13。
