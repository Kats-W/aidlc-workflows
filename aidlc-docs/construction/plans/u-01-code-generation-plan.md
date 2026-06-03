# U-01 Core Infrastructure — Code Generation Plan
# au Jibun Bank AI Agent

## 計画メタデータ
- **ユニット**: U-01 Core Infrastructure
- **フェーズ**: Code Generation
- **CDK スタック**: `SharedInfraStack`（TypeScript / CDK v2）
- **Python パッケージ**: `au-jibun-bank-ai-agent`（Python 3.12 + uv）
- **リージョン**: ap-northeast-1
- **状態**: 全質問は確定済みコンテキストから解決済み。ユーザーへの追加質問なし。

---

## ストーリートレーサビリティ

U-01 は **横断的なコアインフラストラクチャ** ユニットであり、機能ユーザーストーリーを直接実装しない。
- 割り当て User Story: **なし**（インフラ基盤のみ）
- U-01 が提供する基盤（DynamoDB / KMS / S3 / Secrets / VPC / IAM / Connect / Lex / SSM / 共通例外階層）は U-02〜U-06 の全ストーリーから参照される。
- 例外階層 `src/common/errors.py` は後続全ユニットの共通依存。

---

## 実行チェックリスト

### フェーズ A: Python プロジェクト基盤
- [x] Step A1: `pyproject.toml` 生成（依存・dev依存・ruff・mypy・pytest 設定）
- [x] Step A2: `src/__init__.py`（空）
- [x] Step A3: `src/common/__init__.py`（errors 再エクスポート）
- [x] Step A4: `src/common/errors.py`（AppError 例外階層 全25クラス: AppError + 24 サブクラス）

### フェーズ B: Python テスト
- [x] Step B1: `tests/__init__.py`（空）
- [x] Step B2: `tests/unit/__init__.py`（空）
- [x] Step B3: `tests/unit/common/__init__.py`（空）
- [x] Step B4: `tests/unit/common/test_errors.py`（単体テスト・継承チェーン・retryable 区別）
- [x] Step B5: `tests/unit/common/test_errors_property.py`（hypothesis PBT）

### フェーズ C: CDK TypeScript インフラ
- [x] Step C1: `infra/package.json`
- [x] Step C2: `infra/tsconfig.json`
- [x] Step C3: `infra/cdk.json`
- [x] Step C4: `infra/bin/app.ts`（エントリポイント・将来スタックのプレースホルダー）
- [x] Step C5: `infra/lib/stacks/shared_infra_stack.ts`（完全実装 12 リソース群）

### フェーズ D: CI/CD
- [x] Step D1: `.github/workflows/ci.yml` に `python-ci` / `cdk-ci` ジョブを追加（既存 markdownlint ジョブは保持）

### フェーズ E: 確認
- [x] Step E1: `pyproject.toml` の TOML 妥当性確認
- [x] Step E2: `errors.py` の全25クラス定義確認（AppError + 24 サブクラス）
- [x] Step E3: `shared_infra_stack.ts` の TypeScript 構文精査
- [x] Step E4: hypothesis `@given` の正当性確認

---

## 生成ファイル一覧（生成順）

| # | ファイル | 種別 |
| --- | --- | --- |
| 1 | `aidlc-docs/construction/plans/u-01-code-generation-plan.md` | 計画 |
| 2 | `pyproject.toml` | Python 設定 |
| 3 | `src/__init__.py` | Python |
| 4 | `src/common/__init__.py` | Python |
| 5 | `src/common/errors.py` | Python（例外階層） |
| 6 | `tests/__init__.py` | テスト |
| 7 | `tests/unit/__init__.py` | テスト |
| 8 | `tests/unit/common/__init__.py` | テスト |
| 9 | `tests/unit/common/test_errors.py` | 単体テスト |
| 10 | `tests/unit/common/test_errors_property.py` | PBT（hypothesis） |
| 11 | `infra/package.json` | CDK |
| 12 | `infra/tsconfig.json` | CDK |
| 13 | `infra/cdk.json` | CDK |
| 14 | `infra/bin/app.ts` | CDK エントリ |
| 15 | `infra/lib/stacks/shared_infra_stack.ts` | CDK スタック |
| 16 | `.github/workflows/ci.yml` | CI/CD（ジョブ追加） |

---

## 主要な実装上の判断

1. **例外 `retryable` 属性**: スロットリング/タイムアウト系（`BedrockThrottledError`, `FetchTimeoutError`, `TimeoutBudgetExceeded`）を `retryable = True`、それ以外を `False` とし、リトライ制御を型で表現。
2. **Python 3.12 型スタイル**: `Optional[x]` 不使用、`str | None` を使用。
3. **CI 統合**: 既存 `ci.yml`（markdownlint）を破壊せず `python-ci` / `cdk-ci` ジョブを追記。
4. **RemovalPolicy**: dev=DESTROY、staging/prod=RETAIN、KMS/IAM 境界=常時 RETAIN。
5. **IAM 最小権限**: 権限境界はサービス別アクションを列挙し `"*"` アクションを排除。
</content>
</invoke>
