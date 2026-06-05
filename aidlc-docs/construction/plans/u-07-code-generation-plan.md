# U-07 Admin Dashboard — Code Generation Plan

- [x] `src/dashboard_api/__init__.py` を生成する
- [x] `src/dashboard_api/handler.py`（DashboardApiLambda: ルーティング/提案一覧/PATCH/metrics/CSV）を生成する
- [x] `src/dashboard_api/metrics_aggregator.py`（MetricsAggregatorLambda: 期間集計・0件境界）を生成する
- [x] `frontend/`（React + Vite + Amplify + Recharts SPA）を生成する
- [x] `infra/lib/stacks/dashboard_stack.ts`（Cognito + HTTP API + 2 Lambda + Scheduler + Amplify + アラーム）を生成する
- [x] `infra/bin/app.ts` に DashboardStack を追加する
- [x] `tests/unit/dashboard_api/__init__.py` を生成する
- [x] `tests/unit/dashboard_api/test_handler.py`（GET/PATCH/metrics 正常・異常、ページング PBT）を生成する
- [x] `tests/unit/dashboard_api/test_metrics_aggregator.py`（0件境界必須）を生成する
- [x] 対象ユーザーストーリー: US-7.1, US-7.2, US-7.3

## 成果物

| ファイル | 内容 |
| --- | --- |
| `src/dashboard_api/handler.py` | DashboardApiLambda — HTTP API ルーティング、gsi_week Query、priorityScore 降順、ページング、PATCH 条件更新、CSV（インジェクション対策）、エラー→HTTP マッピング |
| `src/dashboard_api/metrics_aggregator.py` | MetricsAggregatorLambda — CustomerHistory 期間集計（チャネル別/エスカレーション率/CSAT/平均ターン/AI解決率）、0件は0/null |
| `frontend/**` | package.json/tsconfig/vite/index.html、App（withAuthenticator）、ApiClient（IDトークン+自動リフレッシュ）、SuggestionListView、MetricsView、SuggestionStatusControl（data-testid 付与） |
| `infra/lib/stacks/dashboard_stack.ts` | DashboardStack（Cognito MFA OPTIONAL、HTTP API + JWT authorizer、2 Lambda、Scheduler、Amplify CfnApp、アラーム、最小権限 IAM、SSM 出力） |
| `infra/bin/app.ts` | DashboardStack をアプリに登録 |
| `tests/unit/dashboard_api/test_*.py` | handler 正常/異常 + metrics 0件境界 + ページング totalPages 整合性 PBT |
| `aidlc-docs/construction/u-07/**` | functional / nfr-requirements / nfr-design / infrastructure 設計 |

## 検証

```bash
uv run ruff check src/ tests/
uv run mypy src/ --ignore-missing-imports
uv run pytest tests/unit/dashboard_api/ -v
(cd infra && npm install && npx tsc --noEmit)
```

## 禁止事項の遵守

- `pickle` 不使用
- Python 3.12 型スタイル（`x | None`、`Optional` 不使用、`from __future__ import annotations`）
- IAM `"*"` アクション不使用（テーブル/index/Aggregator ARN にスコープ）
- frontend は実ビルドしない（コード生成のみ）
- `infra/package-lock.json` はコミットしない
