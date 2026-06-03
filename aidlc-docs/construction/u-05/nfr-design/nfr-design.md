# U-05 SDK & Customer Profile — NFR Design

NFR Requirements の各項目を満たす設計上の実装方針。

## 1. Privacy: au ID を漏らさない設計 (SEC-1)

- ハッシュ化は `IdentityHasher.hash_au_id` の単一地点に集約。`handler._resolve_customer_id`
  は例外時もエラーコードのみログ出力し、平文 au ID を `extra` に載せない。
- `ValidationError` のメッセージは固定文言 `"au_id must not be empty"`（入力をエコーしない）。
- 戻り値は customerId（ハッシュ）のみ。テスト `test_plaintext_au_id_never_in_result` で
  平文非混入を検証。

## 2. Secrets: API キー取得とキャッシュ (SEC-2, PERF-3)

- `CrmClient._get_api_key()` が `self._api_key is None` のときのみ Secrets Manager を呼び、
  以降はインスタンス変数にキャッシュ。ウォーム Lambda（同一実行コンテキスト）で 1 回に抑制。
- キーは `Authorization: Bearer` ヘッダにのみ使用。ログには出力しない。
- 取得失敗は `SecretsError`、未設定は `ConfigError` で明示的に分離。

## 3. Resilience: リトライ・バックオフ・DLQ (REL-2/3/4)

- `CrmClient._post_with_retries` がステータスで分岐:
  - 2xx → 成功（record_id 抽出）
  - 4xx → 即 `CrmApiError`（終端、リトライ無し）
  - 5xx / `httpx.HTTPError` → `last_exc` 保持しバックオフ後リトライ
- バックオフは `_BACKOFF_BASE_SECONDS * 2**(attempt-1)` = 2s, 4s（試行 1,2 の後）。3 試行で
  枯渇時は `CrmApiError`。
- `_process_record` が終端失敗・パース失敗を捕捉し `_send_to_dlq` で DLQ 退避。バッチの他
  レコードは継続処理（部分失敗耐性）。
- インフラ側でも SQS `maxReceiveCount=3` の redrive を併設（多層防御）。

## 4. Graceful degradation (REL-1)

- `handler` は `TimeoutError`（`asyncio.wait_for`）と `AppError` を捕捉し、
  `{"customer_id": ..., "tier": None, "found": False}` を返す。Connect へは決して raise しない。
- RAG ハンドラ（U-03）と同一の「never raise to Connect」方針で一貫。

## 5. Performance budgets (PERF-1/2)

- プロファイル参照は `asyncio.wait_for(..., timeout=6.0)`。Lambda 自体は 10 秒。
- DynamoDB I/O は `asyncio.to_thread` でブロッキング boto3 をオフロード（イベントループ非占有）。
- CrmWriter は 30 秒タイムアウト。POST は 10 秒/試行。バックオフ最大 6 秒 + POST に十分。

## 6. Observability (OBS-1/2/3)

- 各 Lambda 環境変数に `POWERTOOLS_SERVICE_NAME` と `LOG_LEVEL=INFO`。
- 主要分岐（anonymous 判定・no profile・timeout・CRM 成功/失敗/DLQ）で構造化ログ。
- CloudWatch `Errors` アラーム（CustomerProfile: threshold 5 / CrmWriter: threshold 3、
  5 分粒度）。

## 7. Testing strategy (QA-1〜4)

- `test_hasher.py`: hypothesis PBT 4 種 + 空/空白パラメタライズ + 平文非漏洩。
- `test_handler.py`: moto DynamoDB（GSI 付き）で既知/未知/プロファイル無/anonymous/障害降格。
- `test_crm_writer.py`: `httpx.MockTransport` で 2xx/4xx/5xxリトライ/枯渇、キーキャッシュ、
  moto SQS で DLQ。`asyncio.sleep` を patch しバックオフ秒数を検証（テスト高速化）。
