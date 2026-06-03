# U-05 SDK & Customer Profile — Functional Design

## 1. Scope

U-05 は au Jibun Bank AI Agent における「顧客識別・プロファイル付与」と「CRM への
会話サマリー連携」を担当する。担当ユーザーストーリーは次の 3 件。

| Story | 概要 |
|---|---|
| US-5.1 | ネイティブアプリ（au IDログイン済み）からのチャット開始（SDK基盤） |
| US-5.2 | 顧客プロファイル属性付与（au ID ハッシュ → customerId → Connect 属性） |
| US-6.3 | 会話終了後の CRM への会話サマリー非同期書き込み（DLQ 付き） |

## 2. Components

| Component | File | 種別 |
|---|---|---|
| IdentityHasher | `src/profile/hasher.py` | 純粋関数（ドメインサービス） |
| CustomerProfileLambda | `src/profile/handler.py` | Connect contact-flow Lambda |
| CrmWriterLambda + CrmClient | `src/profile/crm_writer.py` | SQS トリガー Lambda + HTTP クライアント |

## 3. Domain Entities

### 3.1 au ID（入力・PII）
- ネイティブアプリ SDK が au ID ログイン済みセッションから取得し、Connect コンタクト属性
  `Details.ContactData.Attributes.auId` として渡す。
- **PII**。平文での保存・ログ出力は禁止。

### 3.2 customerId（派生キー）
- `customerId = SHA-256_hex(au_id)`。64 文字小文字 16 進数。
- 決定論的（ソルト無し）であり、同一 au ID は常に同一 customerId に写像される。
  これにより CustomerHistory のパーティションキーとして横断的に利用できる。
- au ID が無い／無効な場合のセンチネルは `anonymous`。

### 3.3 Customer Profile item（CustomerHistory）
- パーティション `customerId`、ソートキー `sk = "PROFILE"`。
- 属性: `tier`（ロイヤルティ階層など）他。GSI `gsi-customer-id` 経由で参照。

### 3.4 CRM Summary message（SQS body, JSON）
```json
{"customerId": "...", "contactId": "...", "summary": "...", "channel": "chat|voice", "timestamp": "ISO8601"}
```

## 4. Business Logic Model

### 4.1 IdentityHasher.hash_au_id(au_id) -> str
1. `au_id` が空または空白のみ → `ValidationError`（メッセージに平文を含めない）。
2. それ以外 → `sha256(au_id.encode("utf-8")).hexdigest()` を返す。

### 4.2 CustomerProfileLambda.handler(event) (US-5.1 / US-5.2)
1. `event.Details.ContactData.Attributes.auId` を読む。
2. 不在／空 → `customer_id = "anonymous"`、即 `{"customer_id": "anonymous", "tier": None, "found": False}`。
3. 存在 → `IdentityHasher.hash_au_id`。`ValidationError` 時は anonymous へ降格（平文ログ無し）。
4. CustomerHistory を GSI `gsi-customer-id` で `customerId` + `sk = "PROFILE"` 参照（6秒 `asyncio.wait_for`）。
5. タイムアウト／`AppError` → `found=False` に降格（Connect には例外を投げない）。
6. 戻り値 `{"customer_id": str, "tier": str | None, "found": bool}`。

### 4.3 CrmWriterLambda.handler(event) (US-6.3)
1. SQS バッチの各レコードを独立処理。body を JSON パース（失敗 → DLQ）。
2. `customerId` が空または `anonymous` → スキップ（`written=False`）。
3. `CrmClient.post_summary(message)`:
   - Secrets Manager から API キー取得（ウォーム Lambda 内でキャッシュ）。
   - `CRM_ENDPOINT` へ httpx 非同期 POST。
   - 2xx → `crm_record_id` を抽出して返す。
   - 4xx → `CrmApiError`（リトライ無し・終端）。
   - 5xx / ネットワーク → 指数バックオフ（2s→4s→8s, 最大 3 試行）でリトライ。
4. 終端失敗（`CrmApiError`）→ 元メッセージを SQS DLQ へ送信し `written=False`。
5. 戻り値（最後に処理したレコードの結果）`{"written": bool, "crm_record_id": str | None}`。

## 5. Business Rules

- **R1**: 平文 au ID をログ・戻り値・エラーメッセージに含めない。
- **R2**: 顧客識別不能時は `anonymous` で処理を継続（フローを止めない）。
- **R3**: anonymous 顧客の会話サマリーは CRM へ書き込まない。
- **R4**: プロファイル参照失敗は Connect へ例外を投げず graceful degrade（`found=False`）。
- **R5**: CRM 書き込みはライブ接客パスから分離（非同期 SQS）し、CRM 障害が顧客体験に波及しない。
- **R6**: customerId は決定論的ハッシュ（同一入力 → 同一出力）。

## 6. Error Handling

`src/common/errors.py` の `AppError` 階層を再利用:

| 状況 | 例外 | retryable |
|---|---|---|
| 空 au ID | `ValidationError` | No |
| DynamoDB 参照失敗 | `DynamoAccessError` | No |
| CRM 4xx / リトライ枯渇 | `CrmApiError` | No |
| Secrets 取得失敗 | `SecretsError` | No |
| 設定（endpoint/secret）欠落 | `ConfigError` | No |
