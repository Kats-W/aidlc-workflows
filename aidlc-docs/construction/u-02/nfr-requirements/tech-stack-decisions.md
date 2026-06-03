# U-02 Tech Stack Decisions — Knowledge Pipeline

---

## 選定一覧

| 領域 | 採用 | 不採用 | 根拠 |
| --- | --- | --- | --- |
| HTTP クライアント | `httpx` (async) | `requests`（同期）, `aiohttp` | 非同期 + 同一 API、プロジェクト規約で同期 requests 禁止 |
| HTML 解析 | `BeautifulSoup4` (html.parser) | `scrapy`, `lxml` 必須化 | 軽量、Lambda パッケージに収まる。scrapy は規約で禁止 |
| robots.txt | `urllib.robotparser` (標準) | サードパーティ | 標準ライブラリで十分、依存削減 |
| 埋め込みモデル | Titan Embeddings v2 (`amazon.titan-embed-text-v2:0`, 1024d) | OpenAI embeddings | `openai` 禁止、Bedrock ネイティブ |
| ベクトル演算 | `numpy` | `pinecone`, `faiss` | `pinecone-client` 禁止。コーパス規模が小さく numpy で十分 |
| ベクトルストア | DynamoDB (VectorStore) | Pinecone, OpenSearch | SharedInfra で provisioning 済み、運用統一 |
| キャッシュ形式 | numpy `.npy` + JSON | **pickle（禁止）**, msgpack | セキュリティ要件（任意コード実行回避） |
| LLM フレームワーク | なし（直接 SDK） | `langchain` | `langchain` 禁止、薄い自前実装で制御性確保 |
| ログ | `aws-lambda-powertools` Logger | print, logging 直 | 構造化 JSON ログの標準化 |
| AWS SDK | `boto3`（`asyncio.to_thread` でオフロード） | aioboto3 全面採用 | boto3 は安定。非同期化は to_thread で達成 |
| テスト | pytest + pytest-asyncio + moto + hypothesis | unittest 単独 | 非同期・AWS モック・PBT を統合 |

---

## 型・スタイル

- Python 3.12 型: `list[float]`, `dict[str, Any]`, `str | None`（`Optional` 不使用）。
- 例外は `src/common/errors.py` の既存階層を再利用（新規追加なし）。

---

## CDK

- CDK v2 (TypeScript)、`KnowledgePipelineStack`。
- SharedInfraStack の出力は SSM Parameter Store 経由で参照（スタック間の直接依存を避ける）。
