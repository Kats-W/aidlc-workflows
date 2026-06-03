# U-05 SDK & Customer Profile — NFR Requirements

## 1. Security & Privacy

- **SEC-1 (au ID 機密性)**: au ID は PII。平文での永続化・ログ出力・メトリクス化を一切
  禁止。下流には SHA-256 ハッシュ（customerId）のみを伝播する。
- **SEC-2 (認証情報)**: CRM API キーはハードコード禁止。Secrets Manager から取得し、
  ウォーム Lambda 内のみでメモリキャッシュ。ログ・例外メッセージへの出力禁止。
- **SEC-3 (最小権限 IAM)**: 両 Lambda の実行ロールは `"*"` アクションを含まない。
  CustomerProfile は CustomerHistory の `GetItem`/`Query` のみ、CrmWriter は対象
  キュー/DLQ/シークレット/CMK のみ。すべて共有 permission boundary 配下。
- **SEC-4 (転送・保存時暗号化)**: SQS キューは KMS（共有 CMK）暗号化、`enforceSSL`。
  CRM への POST は HTTPS。

## 2. Reliability

- **REL-1 (graceful degradation)**: プロファイル参照の失敗・タイムアウトは Connect へ
  例外を投げず `found=False` で継続。接客を停止させない。
- **REL-2 (非同期分離)**: CRM 書き込みは SQS 経由で接客パスから分離。CRM 障害が
  顧客対応のレイテンシ/可用性に波及しないこと。
- **REL-3 (リトライ)**: CRM 5xx/ネットワークは指数バックオフ（2s→4s→8s, 最大 3 試行）。
  4xx は終端（リトライしない）。
- **REL-4 (DLQ)**: 終端失敗・ポイズンメッセージは DLQ（保持 14 日）へ退避し、バッチの
  進行を妨げない。

## 3. Performance

- **PERF-1**: CustomerProfile のプロファイル参照は 6 秒バジェット（`asyncio.wait_for`）。
  Connect の 8 秒制限内に収める。Lambda タイムアウトは 10 秒。
- **PERF-2**: CrmWriter の Lambda タイムアウトは 30 秒。1 POST のリクエストタイムアウトは
  10 秒。バックオフ合計（最大 14 秒）+ POST を許容する設計とする。
- **PERF-3**: API キーキャッシュにより Secrets Manager 呼び出しはウォーム実行あたり 1 回。

## 4. Observability

- **OBS-1**: AWS Lambda Powertools Logger による構造化ログ。`POWERTOOLS_SERVICE_NAME`
  を関数ごとに設定（`u-05-customer-profile` / `u-05-crm-writer`）。
- **OBS-2**: 両 Lambda の `Errors` メトリクスに CloudWatch アラーム。
- **OBS-3**: ログには customerId（ハッシュ）・contactId・エラーコードのみ。平文 au ID と
  API キーは含めない。

## 5. Testability / Quality

- **QA-1**: pytest + pytest-asyncio（`asyncio_mode = "auto"`）。AWS は moto でモック。
- **QA-2**: IdentityHasher は hypothesis による PBT（64文字16進・決定論性・衝突回避・
  SHA-256 参照一致）。
- **QA-3**: ライン カバレッジ目標 80% 以上（実績 89%）。
- **QA-4**: ruff（E/F/I/UP/B/C4/SIM/RUF）・mypy strict を通過。

## 6. Maintainability / Standards

- **STD-1**: Python 3.12 typing（`x | None`、`Optional` 禁止）。
- **STD-2**: 同期 HTTP（requests）禁止 → httpx 非同期。pickle 禁止。
- **STD-3**: 共有 `AppError` 階層（`ValidationError`/`CrmApiError`/`SecretsError`/
  `ConfigError`/`DynamoAccessError`）を再利用。
- **STD-4**: 横断リソース名/ARN は SharedInfraStack の SSM パラメータから解決。
