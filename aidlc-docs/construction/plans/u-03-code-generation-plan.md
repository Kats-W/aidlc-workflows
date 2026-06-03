# U-03 AI Conversation Engine — Code Generation Plan

- [x] `src/common/bedrock_client.py` を拡張（`generate_answer` / `analyze_gap` を追加）
- [x] `src/common/pii_masker.py`（Comprehend PII マスク）を生成
- [x] `src/rag_handler/`（handler / personalizer / escalation）を生成
- [x] `src/session_manager/`（history / csat_handler）を生成
- [x] `infra/lib/stacks/conversation_stack.ts` + `infra/bin/app.ts` 更新
- [x] テスト（rag_handler / session_manager / pii_masker、moto + hypothesis + mock）を生成

## 禁止事項の遵守

| 項目 | 対応 |
| --- | --- |
| `pickle` 禁止 | 未使用（履歴は DynamoDB、キャッシュは npy/JSON） |
| 同期 `requests` 禁止 | boto3 同期呼び出しを `asyncio.to_thread` でラップ |
| Python 3.12 型 | `x | None` / `list[str]`、`Optional` 不使用 |
| IAM `"*"` アクション禁止 | リソース指定。Comprehend Detect* のみ `Resource: "*"`（API 制約） |
| ハードコード認証情報禁止 | 認証情報は環境/IAM ロール経由のみ |

## 検証コマンド

```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run mypy src/
```
