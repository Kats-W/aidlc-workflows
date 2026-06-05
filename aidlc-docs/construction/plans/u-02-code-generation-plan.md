# U-02 Knowledge Pipeline — Code Generation Plan

# au Jibun Bank AI Agent

## 計画メタデータ

- **ユニット**: U-02 Knowledge Pipeline
- **フェーズ**: Code Generation
- **CDK スタック**: `KnowledgePipelineStack`（TypeScript / CDK v2）
- **Python パッケージ**: `au-jibun-bank-ai-agent`（Python 3.12 + uv）
- **リージョン**: ap-northeast-1
- **状態**: 確定済みコンテキストから全質問解決済み。追加質問なし。

---

## ストーリートレーサビリティ

| User Story | 実装 | テスト |
| --- | --- | --- |
| US-2.1 週次クロール | `robots.py`, `parser.py`, `crawler/handler.py`, Scheduler | `test_robots.py`, `test_parser.py` |
| US-2.2 差分更新 | `differ.py`, `s3_store.py`, `vector_store/handler.py`, `store.py` | `test_differ.py`, `test_s3_store.py`, `test_store.py` |
| US-2.3 ベクトル検索 | `searcher.py`, `bedrock_client.py` | `test_searcher.py`, `test_bedrock_client.py` |

---

## 実行チェックリスト

### フェーズ A: クローラ Python ソース

- [x] Step A1: `src/crawler/__init__.py`（空）
- [x] Step A2: `src/crawler/robots.py`（RobotsTxtGuard）
- [x] Step A3: `src/crawler/parser.py`（ContentParser + ContentChunk）
- [x] Step A4: `src/crawler/differ.py`（DifferEngine + DiffResult）
- [x] Step A5: `src/crawler/s3_store.py`（S3ContentStore）
- [x] Step A6: `src/crawler/handler.py`（CrawlerLambda）

### フェーズ B: ベクトルストア Python ソース

- [x] Step B1: `src/vector_store/__init__.py`（空）
- [x] Step B2: `src/vector_store/store.py`（VectorStore, Decimal 変換）
- [x] Step B3: `src/vector_store/searcher.py`（CosineSimilaritySearcher + SearchHit, /tmp キャッシュ）
- [x] Step B4: `src/vector_store/handler.py`（EmbedderLambda）

### フェーズ C: 共通 Bedrock クライアント

- [x] Step C1: `src/common/bedrock_client.py`（BedrockClient.embed, Titan v2）

### フェーズ D: テスト

- [x] Step D1: `tests/unit/crawler/__init__.py`（空）
- [x] Step D2: `tests/unit/crawler/test_robots.py`（httpx モック・Disallow・失敗）
- [x] Step D3: `tests/unit/crawler/test_parser.py`（変換・SHA-256・空 HTML・PBT 決定論）
- [x] Step D4: `tests/unit/crawler/test_differ.py`（moto・新規/変更/削除・PBT 冪等）
- [x] Step D5: `tests/unit/crawler/test_s3_store.py`（moto S3・put/get/delete）
- [x] Step D6: `tests/unit/vector_store/__init__.py`（空）
- [x] Step D7: `tests/unit/vector_store/test_store.py`（moto・Decimal 変換）
- [x] Step D8: `tests/unit/vector_store/test_searcher.py`（/tmp TTL・コサイン PBT・top-k）
- [x] Step D9: `tests/unit/common/test_bedrock_client.py`（embed 正常/エラー）

### フェーズ E: CDK TypeScript

- [x] Step E1: `infra/lib/stacks/knowledge_pipeline_stack.ts`（KnowledgePipelineStack）
- [x] Step E2: `infra/bin/app.ts` に U-02 スタックを配線

### フェーズ F: 確認

- [x] Step F1: `uv run pytest tests/unit/crawler tests/unit/vector_store tests/unit/common/test_bedrock_client.py`（全 38 件 PASS）
- [x] Step F2: `uv run ruff check`（全 PASS）
- [x] Step F3: `uv run mypy src`（no issues）

---

## 生成ファイル一覧（生成順）

| # | ファイル | 種別 |
| --- | --- | --- |
| 1 | `src/crawler/__init__.py` | Python |
| 2 | `src/crawler/robots.py` | Python |
| 3 | `src/crawler/parser.py` | Python |
| 4 | `src/crawler/differ.py` | Python |
| 5 | `src/crawler/s3_store.py` | Python |
| 6 | `src/crawler/handler.py` | Python（Lambda） |
| 7 | `src/vector_store/__init__.py` | Python |
| 8 | `src/vector_store/store.py` | Python |
| 9 | `src/vector_store/searcher.py` | Python |
| 10 | `src/vector_store/handler.py` | Python（Lambda） |
| 11 | `src/common/bedrock_client.py` | Python |
| 12 | `tests/unit/crawler/__init__.py` | テスト |
| 13 | `tests/unit/crawler/test_robots.py` | 単体テスト |
| 14 | `tests/unit/crawler/test_parser.py` | 単体 + PBT |
| 15 | `tests/unit/crawler/test_differ.py` | 単体 + PBT |
| 16 | `tests/unit/crawler/test_s3_store.py` | 単体テスト |
| 17 | `tests/unit/vector_store/__init__.py` | テスト |
| 18 | `tests/unit/vector_store/test_store.py` | 単体テスト |
| 19 | `tests/unit/vector_store/test_searcher.py` | 単体 + PBT |
| 20 | `tests/unit/common/test_bedrock_client.py` | 単体テスト |
| 21 | `infra/lib/stacks/knowledge_pipeline_stack.ts` | CDK スタック |
| 22 | `infra/bin/app.ts` | CDK 配線（更新） |

---

## 主要な実装上の判断

1. **pickle 全面排除**: /tmp キャッシュは `np.save`/`np.load` + JSON。SEC-1 準拠。
2. **robots 製品トークン**: `RobotFileParser` のグループ照合がバージョン付き UA で失敗するため、照合用に `AuJibunBankBot`（版なし）を使用、HTTP ヘッダは `AuJibunBankBot/1.0`。
3. **非同期 + boto3**: 公開 API は `async`、boto3 同期呼び出しは `asyncio.to_thread` でオフロード。
4. **差分はチャンク粒度**: SHA-256 比較で added/changed/deleted を算出し、冪等。
5. **Decimal 格納**: embedding は DynamoDB 数値型のため `Decimal` リストで格納し、読み出し時に float へ復元。
6. **最小権限 IAM**: Bedrock を単一 modelId に限定、`"*"` アクションを排除。
7. **SSM 疎結合**: U-01 SharedInfra の出力を SSM 経由で参照。
8. **Lambda エントリ**: `async def handler` + 同期 `lambda_handler`（`asyncio.run` ラッパ）の二層構造。
