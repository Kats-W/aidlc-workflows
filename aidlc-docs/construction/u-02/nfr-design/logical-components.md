# U-02 Logical Components — Knowledge Pipeline

---

## コンポーネント分解

```
src/
├─ crawler/
│   ├─ robots.py     RobotsTxtGuard      ← httpx, urllib.robotparser
│   ├─ parser.py     ContentParser       ← BeautifulSoup4   (ContentChunk 定義)
│   ├─ differ.py     DifferEngine        ← DynamoDB (ContentDiff)  (DiffResult 定義)
│   ├─ s3_store.py   S3ContentStore      ← S3
│   └─ handler.py    CrawlerLambda       ← 上記全て + Lambda Invoke
├─ vector_store/
│   ├─ store.py      VectorStore         ← DynamoDB (VectorStore)
│   ├─ searcher.py   CosineSimilaritySearcher ← numpy + /tmp cache  (SearchHit 定義)
│   └─ handler.py    EmbedderLambda      ← VectorStore + BedrockClient
└─ common/
    ├─ errors.py        例外階層（U-01 既存）
    └─ bedrock_client.py BedrockClient.embed ← Bedrock Runtime (Titan v2)
```

---

## 依存関係（レイヤ）

| レイヤ | コンポーネント | 依存先 |
| --- | --- | --- |
| エントリ | `CrawlerLambda`, `EmbedderLambda` | ドメイン層全て |
| ドメイン | `DifferEngine`, `CosineSimilaritySearcher` | アダプタ層 |
| アダプタ | `RobotsTxtGuard`, `ContentParser`, `S3ContentStore`, `VectorStore`, `BedrockClient` | 外部 AWS / HTTP |
| 共通 | `errors` | なし |

- 依存方向はエントリ → ドメイン → アダプタ → 共通 の単方向。
- すべてのアダプタはコンストラクタにクライアント/テーブルを注入可能（テスト容易性）。

---

## 並行性モデル

- 公開 API はすべて `async def`。
- boto3（同期）の呼び出しは `asyncio.to_thread` でスレッドプールへオフロードしイベントループをブロックしない。
- Lambda エントリは `lambda_handler`（同期）が `asyncio.run(handler(...))` をラップ。

---

## 設定（環境変数）

| 変数 | 利用コンポーネント |
| --- | --- |
| `CRAWLER_TARGET_URLS` (JSON list) | CrawlerLambda |
| `CONTENT_DIFF_TABLE_NAME` | CrawlerLambda / DifferEngine |
| `CRAWL_CONTENT_BUCKET` | CrawlerLambda / EmbedderLambda / S3ContentStore |
| `EMBEDDER_FUNCTION_NAME` | CrawlerLambda（Invoke 先） |
| `VECTOR_STORE_TABLE_NAME` | EmbedderLambda / VectorStore |

---

## テスト容易性

| コンポーネント | テスト手段 |
| --- | --- |
| `RobotsTxtGuard` | httpx を `unittest.mock` でモック |
| `ContentParser` | 実 HTML 文字列 + hypothesis PBT |
| `DifferEngine` / `VectorStore` | moto DynamoDB |
| `S3ContentStore` | moto S3 |
| `CosineSimilaritySearcher` | スタブ store + /tmp 実ファイル + hypothesis PBT |
| `BedrockClient` | boto3 クライアントを `MagicMock` |
