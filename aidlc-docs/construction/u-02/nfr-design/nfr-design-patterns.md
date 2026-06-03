# U-02 NFR Design Patterns — Knowledge Pipeline

---

## 1. 指数バックオフ + ジッタ（リトライ）

リトライ可否は `src.common.errors` の `retryable` 属性で型として表現する。

| 例外 | retryable | バックオフ対象 |
| --- | --- | --- |
| `BedrockThrottledError` | True | ○ |
| `FetchTimeoutError` | True | ○ |
| `TimeoutBudgetExceeded` | True | ○ |
| `ParseError` / `DynamoAccessError` / `S3AccessError` 他 | False | × |

推奨バックオフ式（呼び出し側 / Step Functions 側で適用）:

```
delay = min(base * 2**attempt, cap) + random_jitter
base = 0.5s, cap = 8s, max_attempts = 5
```

- EventBridge Scheduler のターゲットリトライは最大 2 回（インフラ層の粗粒度リトライ）。
- アプリ層では Bedrock/HTTP のスロットリングを `retryable=True` の例外で識別。

---

## 2. /tmp キャッシュ TTL パターン

```
CACHE_VECTORS = "/tmp/vectors.npy"     # numpy 行列 (N × 1024)
CACHE_META    = "/tmp/vectors_meta.json"  # [{chunkId, sourceUrl, text}, ...]
CACHE_TS      = "/tmp/vectors_ts.txt"  # str(time.time())
TTL = 900  # 15 分
```

判定ロジック:

```
valid = exists(all 3 files) and (now - read(ts)) < TTL
```

- **pickle 不使用**: `np.save` / `np.load` と `json` のみ。
- 破損・欠損時は DynamoDB から再構築し 3 ファイルを上書き。
- 書き込み失敗は検索を止めない（警告ログのみ）。
- Lambda コンテナ再利用（ウォームスタート）で全件スキャンを回避し PERF-2 を満たす。

---

## 3. Polite Crawling パターン

- robots.txt をホスト単位で 1 回だけロードしキャッシュ（同一実行内）。
- `User-Agent: AuJibunBankBot/1.0`、robots 照合は製品トークン `AuJibunBankBot`。
- 各 URL 取得後 `asyncio.sleep(random.uniform(1, 3))`。
- robots 未ロード時は fail-safe deny。

---

## 4. 冪等性パターン（差分）

- 差分はチャンク `content_hash` の集合比較で算出 → 同一クロール再適用で空差分。
- `commit` は `batch_writer` による upsert / delete で、再実行しても最終状態が一意。
- VectorStore `upsert` は `put_item`（全置換）で重複無し、`delete` は存在しなくても無害。

---

## 5. エラー変換パターン

| 起点 | 変換先例外 |
| --- | --- |
| `httpx.HTTPError` | `FetchTimeoutError` |
| `botocore ClientError`(throttle) | `BedrockThrottledError` |
| `botocore ClientError`(other, Bedrock) | `EmbeddingError` |
| `botocore ClientError`(DynamoDB) | `DynamoAccessError` |
| `botocore ClientError`(S3 NoSuchKey) | `ObjectNotFoundError` |
| 抽出本文空 | `ParseError` |
| クエリ不正・次元不一致 | `SearchError` |
