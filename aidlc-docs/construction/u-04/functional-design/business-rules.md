# U-04 Omnichannel & Escalation — Business Rules

# （ContactId 維持・20ターン上限・PII・エスカレーション）

U-04 が強制するビジネスルール。違反は実装・テストで防止する。

---

## BR-4.1 同一 ContactId の維持（US-4.1 / US-4.2）

- チャネル切り替え前後で **Amazon Connect ContactId を変更しない**。
- `SESSION#<contactId>` の PK は `contactId` をそのまま使用する。
  匿名セッションのみ `ANON#<contactId>` を PK とする。
- これにより音声⇔チャット間で文脈（`turns`）が一貫して引き継がれる。

## BR-4.2 セッションターン保持上限

- `SESSION#` の `turns` は **最大 20 ターン**を保持（`MAX_TURNS = 20`）。
- 追記時に上限を超える場合は **古いターンから破棄**（新しい 20 件を残す）。
- 上限はレイテンシ・項目サイズ（DynamoDB 400KB 制限）・要約品質のバランスで決定。

## BR-4.3 PII マスク済み前提

- `SESSION#` に書き込む `text` は **呼び出し側で PII マスク済み**であること（U-03 ルールを継承）。
- U-04 は再マスクを行わず、マスク済みデータの永続化・読み出しのみを担う。

## BR-4.4 引き継ぎサマリー書式

- 要約は直近 `last_n`（既定 5）ターンを **古い順**に整形する。
- `role == "user"` → `顧客: <text>`、それ以外 → `AI: <text>`。
- 1 ターン 1 行、改行（`\n`）区切り。ターンが無ければ空文字を返す。

## BR-4.5 セッション未存在時の挙動

- `SessionContextManager.get()` はセッションが無ければ `SessionNotFoundError` を送出。
- `handler()` は `SessionNotFoundError` を捕捉し、**空サマリー・turn_count=0 で新規セッション開始**として正常応答する（切り替えを失敗させない）。

## BR-4.6 入力検証

- `contactId` が空の場合は `ValidationError` を送出（`handler` および `SessionContextManager.get/update`）。

## BR-4.7 有人エスカレーション（US-4.3）

- エスカレーション先キュー ARN は **SSM Parameter Store** から解決（ハードコード禁止）。
- TransferToQueue は Connect contact flow 側で実行し、U-04 はキュー ARN の配線・出力のみ担う。

## BR-4.8 データ保持

- `SESSION#` 項目は `expiresAt`（TTL）で **90 日**後に自動失効する（CustomerHistory 共通ポリシー）。
