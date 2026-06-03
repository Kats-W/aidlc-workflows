# U-04 Omnichannel & Escalation — Code Generation Plan

- [x] `src/session_manager/channel_switch.py`（SessionContext・SessionContextManager・handler）を生成する
- [x] `infra/lib/stacks/omnichannel_stack.ts`（OmnichannelStack）を生成する
- [x] `infra/bin/app.ts` に OmnichannelStack を追加する
- [x] `tests/unit/session_manager/test_channel_switch.py`（moto + hypothesis）を生成する
- [x] 対象ユーザーストーリー: US-4.1, US-4.2, US-4.3

## 成果物

| ファイル | 内容 |
| --- | --- |
| `src/session_manager/channel_switch.py` | チャネル切り替え・文脈引き継ぎロジック |
| `infra/lib/stacks/omnichannel_stack.ts` | ChannelSwitchLambda + エスカレーションキュー配線 |
| `infra/bin/app.ts` | OmnichannelStack をアプリに登録 |
| `tests/unit/session_manager/test_channel_switch.py` | 単体テスト + PBT |

## 検証

```bash
uv run pytest tests/unit/session_manager/ -q
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run mypy src/
```

## 禁止事項の遵守

- `pickle` 不使用（DynamoDB の L/M 型に JSON 互換マップで永続化）
- Python 3.12 型スタイル（`x | None`, `list[str]`、`Optional` 不使用）
- IAM `"*"` アクション不使用（CustomerHistory テーブル ARN にスコープ）
- ハードコード認証情報なし（SSM Parameter Store 経由で名前/ARN を解決）
