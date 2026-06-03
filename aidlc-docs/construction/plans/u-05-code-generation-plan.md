# U-05 SDK & Customer Profile — Code Generation Plan

- [x] `src/profile/__init__.py` を生成する
- [x] `src/profile/hasher.py`（IdentityHasher: SHA-256 au ID ハッシュ）を生成する
- [x] `src/profile/handler.py`（CustomerProfileLambda: Connect プロファイル付与）を生成する
- [x] `src/profile/crm_writer.py`（CrmWriterLambda + CrmClient: 非同期 CRM 書き込み）を生成する
- [x] `infra/lib/stacks/profile_stack.ts`（ProfileStack: 2 Lambda + SQS/DLQ + アラーム + IAM）を生成する
- [x] `infra/bin/app.ts` に ProfileStack を追加する
- [x] `tests/unit/profile/__init__.py` を生成する
- [x] `tests/unit/profile/test_hasher.py`（hypothesis PBT）を生成する
- [x] `tests/unit/profile/test_handler.py`（moto DynamoDB）を生成する
- [x] `tests/unit/profile/test_crm_writer.py`（httpx mock + moto SQS）を生成する
- [x] 対象ユーザーストーリー: US-5.1, US-5.2, US-6.3

## 成果物

| ファイル | 内容 |
| --- | --- |
| `src/profile/hasher.py` | IdentityHasher — 決定論的 au ID → customerId ハッシュ |
| `src/profile/handler.py` | CustomerProfileLambda — au ID 解決 + プロファイル/tier 参照 |
| `src/profile/crm_writer.py` | CrmWriterLambda + CrmClient — 非同期 CRM POST・リトライ・DLQ |
| `infra/lib/stacks/profile_stack.ts` | ProfileStack（CustomerProfile/CrmWriter Lambda・SQS+DLQ・アラーム・最小権限 IAM） |
| `infra/bin/app.ts` | ProfileStack をアプリに登録 |
| `tests/unit/profile/test_hasher.py` | PBT（64文字16進・決定論・衝突回避・SHA-256一致）+ 空入力 |
| `tests/unit/profile/test_handler.py` | 既知/未知/anonymous/障害降格/平文非漏洩 |
| `tests/unit/profile/test_crm_writer.py` | 2xx/4xx/5xxリトライ/枯渇・キーキャッシュ・DLQ |
| `aidlc-docs/construction/u-05/**` | functional / nfr-requirements / nfr-design / infrastructure 設計 |

## 検証

```bash
uv run ruff check src/ tests/
uv run mypy src/ --ignore-missing-imports
uv run pytest tests/unit/profile/ -v
uv run pytest tests/unit/ -q
(cd infra && npx tsc --noEmit && npx cdk synth AuJibunBank-dev-Profile --context env=dev)
```

実績: ruff/mypy クリーン、profile 28 件・全体 246 件パス、profile カバレッジ 89%、
tsc クリーン、cdk synth 成功。

## 禁止事項の遵守

- `pickle` 不使用
- 同期 `requests` 不使用 → httpx 非同期クライアント
- Python 3.12 型スタイル（`x | None`、`Optional` 不使用、`from __future__ import annotations`）
- IAM `"*"` アクション不使用（CustomerHistory / 対象 SQS / シークレット ARN にスコープ）
- ハードコード認証情報なし（CRM API キーは Secrets Manager から取得・キャッシュ）
- 平文 au ID をログ・戻り値・例外メッセージに含めない
