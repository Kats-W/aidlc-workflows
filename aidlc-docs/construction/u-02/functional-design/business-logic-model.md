# U-02 Business Logic Model — Knowledge Pipeline

本ドキュメントは U-02 の中核ビジネスロジック（週次クロール・差分検出・コサイン
類似度検索）のフローを定義する。

---

## 1. 週次クロールフロー（US-2.1）

```
EventBridge Scheduler (週次 Sun 02:00 JST)
        │
        ▼
  CrawlerLambda.handler
        │
        ├─ CRAWLER_TARGET_URLS (env, JSON list) を読み込み
        │
        ├─ ホスト単位で RobotsTxtGuard.load()  ← httpx で robots.txt 取得
        │
        └─ 各 URL について:
              ├─ guard.is_allowed(url) が False → スキップ
              ├─ httpx.AsyncClient.get(url)         ← User-Agent: AuJibunBankBot/1.0
              ├─ ContentParser.parse(html, url)     → list[ContentChunk]
              ├─ 各チャンク本文を S3ContentStore.put()
              └─ asyncio.sleep(uniform(1,3))        ← polite delay
```

- すべての I/O は `async def`。boto3 の同期呼び出しは `asyncio.to_thread` でオフロード。
- 1 URL の失敗（fetch / parse）は `errors[]` に記録し、クロール全体は継続する。

---

## 2. 差分検出フロー（US-2.2）

```
全チャンク (list[ContentChunk])
        │
        ▼
 DifferEngine.diff(new_chunks)
        │
        ├─ ContentDiff テーブルを scan → {chunkId: contentHash} (stored)
        │
        └─ 各 new チャンクを分類:
              ├─ stored に無い            → added
              ├─ contentHash が異なる      → changed
              └─ (stored にあり new に無い) → deleted
        │
        ▼
 DiffResult(added, changed, deleted)
        │
        ├─ DifferEngine.commit() で ContentDiff を最新化（upsert / delete）
        └─ result が空でなければ EmbedderLambda を非同期 Invoke
```

### EmbedderLambda フロー

```
EmbedderLambda.handler(payload={upsert:[...], delete:[...]})
        │
        ├─ upsert 各チャンク: BedrockClient.embed(text) → list[float](1024)
        │                     VectorStore.upsert(chunk, vector)
        └─ delete 各 chunkId: VectorStore.delete(chunkId)
        ▼
   {"upserted": int, "deleted": int}
```

---

## 3. コサイン類似度検索フロー（US-2.3）

```
CosineSimilaritySearcher.search(query_vec, top_k)
        │
        ├─ _load_vectors()  ← 3層キャッシュ（メモリ → /tmp → S3 → DynamoDB）
        │     ├─ メモリキャッシュ有効（TTL 900s 以内）? → インスタンス変数から直接返却
        │     ├─ /tmp キャッシュ有効? → .npy + JSON をロード → メモリにも保持
        │     ├─ 無効 + S3 キャッシュあり → S3 から matrix.npy + meta.json DL
        │     │     → /tmp 保存 + メモリ保持
        │     └─ S3 未構築 → VectorStore.scan_all() → matrix 構築
        │           → /tmp 保存 + メモリ保持
        │
        ├─ _cosine_top_k(matrix, query, k)   ← numpy float32 ベクトル演算
        │     scores = (matrix @ query) / (‖row‖ · ‖query‖)
        │     argpartition で top-k → 降順ソート
        │
        ├─ VectorStore.batch_get_texts(chunk_ids)  ← top-k の text を DynamoDB から取得
        │
        ▼
   list[SearchHit]  (chunk_id, source_url, text, score)
```

- S3 キャッシュは numpy `.npy`（embedding 行列、float32）+ JSON（chunkId + sourceUrl のみ、text なし）の 2 ファイル。
- text はキャッシュに含めず、top-k hit の text のみ DynamoDB BatchGetItem で取得（メモリ効率）。
- 3層キャッシュ戦略: メモリ（インスタンス変数）→ /tmp（.npy + JSON + timestamp）→ S3 → DynamoDB scan_all（フォールバック）。
- メモリキャッシュにより、ウォーム時の /tmp ディスク I/O（~500MB .npy 読み込み ~1,500ms）をスキップ。
- 全演算は float32 で統一（埋め込み保存も float32、float64 アップキャストなし）。
- pickle は使用しない（セキュリティ要件）。
- RagHandler 起動時に _ensure_cache_warmed() が S3 DL + メモリ保持 + DynamoDB warm_connection を 6s バジェット外で実行。
- EmbedderLambda は各バッチで S3 キャッシュをインクリメンタルに更新（O(batch size)、full scan 不要）。

---

## 4. コンポーネント間の協調

| コンポーネント | 責務 | 依存 |
| --- | --- | --- |
| `RobotsTxtGuard` | robots.txt 取得・許可判定 | httpx |
| `ContentParser` | HTML 抽出・チャンク化・ハッシュ | BeautifulSoup4 |
| `DifferEngine` | 差分検出・ContentDiff 同期 | DynamoDB |
| `S3ContentStore` | チャンク本文の永続化 | S3 |
| `BedrockClient` | Titan v2 埋め込み生成 | Bedrock Runtime |
| `VectorStore` | ベクトル CRUD・全件スキャン | DynamoDB |
| `CosineSimilaritySearcher` | コサイン top-k 検索・/tmp キャッシュ | numpy, VectorStore |
