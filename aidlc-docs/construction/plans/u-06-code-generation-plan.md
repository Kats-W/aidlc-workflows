# U-06 Self-Improvement Pipeline — Code Generation Plan

- [x] `src/improvement_generator/__init__.py` を生成する
- [x] `src/improvement_generator/contact_lens_analyzer.py`（ContactLensAnalyzerLambda: 低品質コンタクト検出）を生成する
- [x] `src/improvement_generator/gap_analyzer.py`（GapAnalyzerLambda: ナレッジギャップ分析）を生成する
- [x] `src/improvement_generator/suggestion_generator.py`（SuggestionGeneratorLambda: 改善提案生成）を生成する
- [x] `src/common/bedrock_client.py` の `analyze_gap()` を placeholder から実装へ更新（+ `generate_suggestion()` 追加）
- [x] `infra/lib/stacks/improvement_stack.ts`（ImprovementStack: 3 Lambda + Scheduler + アラーム + IAM）を生成する
- [x] `infra/bin/app.ts` に ImprovementStack を追加する
- [x] `tests/unit/improvement_generator/__init__.py` を生成する
- [x] `tests/unit/improvement_generator/test_contact_lens_analyzer.py` を生成する
- [x] `tests/unit/improvement_generator/test_gap_analyzer.py`（hypothesis PBT: 単調性）を生成する
- [x] `tests/unit/improvement_generator/test_suggestion_generator.py`（hypothesis PBT: 上限10件）を生成する
- [x] 対象ユーザーストーリー: US-3.1, US-3.2, US-3.3

## 成果物

| ファイル | 内容 |
| --- | --- |
| `src/improvement_generator/contact_lens_analyzer.py` | ContactLensAnalyzerLambda — 週次低品質コンタクト検出（CSAT≤2/escalation/NEGATIVE≥0.7）、バックオフ、ContactAnalysis 保存、連鎖 invoke |
| `src/improvement_generator/gap_analyzer.py` | GapAnalyzerLambda — マスク済みサマリー→Claude トピック分類、わかりにくさスコア、降順ソート |
| `src/improvement_generator/suggestion_generator.py` | SuggestionGeneratorLambda — 最大10件提案、pending重複スキップ、TTL90日、条件付き PutItem |
| `src/common/bedrock_client.py` | `analyze_gap()` 実装（JSON 抽出・`ResponseParseError`）+ `generate_suggestion()` |
| `infra/lib/stacks/improvement_stack.ts` | ImprovementStack（3 Lambda・Scheduler・アラーム・最小権限 IAM） |
| `infra/bin/app.ts` | ImprovementStack をアプリに登録 |
| `tests/unit/improvement_generator/test_*.py` | 低品質判定/バックオフ/ハンドラ + PBT（スコア単調性・生成数0〜10） |
| `aidlc-docs/construction/u-06/**` | functional / nfr-requirements / nfr-design / infrastructure 設計 |

## 検証

```bash
uv run ruff check src/ tests/
uv run mypy src/ --ignore-missing-imports
uv run pytest tests/unit/improvement_generator/ -v
uv run pytest tests/unit/ -q
(cd infra && npx tsc --noEmit && npx cdk synth AuJibunBank-dev-Improvement --context env=dev)
```

実績: ruff/mypy クリーン、improvement_generator 36件・全体282件パス、
improvement_generator カバレッジ 91%、tsc クリーン、cdk synth 成功。

## 禁止事項の遵守

- `pickle` 不使用
- 生会話テキスト不使用（ContactLens サマリー属性 / `SUMMARY#` のみ、Claude へはマスク済みサマリー最大50件）
- Python 3.12 型スタイル（`x | None`、`Optional` 不使用、`from __future__ import annotations`）
- IAM `"*"` アクション不使用（各テーブル/index/Sonnet モデル ARN にスコープ）
- 既存 `BedrockClient`（`embed` / `generate_answer`）の互換維持
