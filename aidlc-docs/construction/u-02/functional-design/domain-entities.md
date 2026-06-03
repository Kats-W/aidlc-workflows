# U-02 Domain Entities — Knowledge Pipeline

すべて Python 3.12 `@dataclass` で定義。型は `list[float]` / `str | None` スタイル。

---

## ContentChunk

クロールした 1 ページから抽出・分割された埋め込み単位。

```python
@dataclass(frozen=True, slots=True)
class ContentChunk:
    chunk_id: str       # "{sha256(source_url)}#{index}"
    source_url: str     # 抽出元ページ URL
    index: int          # ページ内 0 始まりの位置
    text: str           # チャンク本文（正規化済み）
    content_hash: str   # text の SHA-256 16 進（差分指紋）
```

| 属性 | 説明 | 制約 |
| --- | --- | --- |
| `chunk_id` | 一意識別子 | VectorStore / ContentDiff の PK |
| `source_url` | 出典 URL | GSI `gsi_sourceUrl` のキー |
| `index` | ページ内位置 | ≥ 0 |
| `text` | 本文 | ≤ 1500 文字 |
| `content_hash` | 指紋 | 64 桁 hex |

---

## DiffResult

差分検出の結果。`added` / `changed` はチャンク本体、`deleted` は ID のみ。

```python
@dataclass(frozen=True, slots=True)
class DiffResult:
    added: list[ContentChunk]
    changed: list[ContentChunk]
    deleted: list[str]          # chunk_id のリスト

    @property
    def is_empty(self) -> bool: ...
```

| 属性 | 説明 |
| --- | --- |
| `added` | 新規チャンク |
| `changed` | ハッシュ変更チャンク |
| `deleted` | 消滅チャンクの ID |
| `is_empty` | 3 つすべて空なら True（埋め込み不要） |

---

## SearchHit

コサイン類似度検索の 1 ヒット。RAG 文脈組み立てに使用。

```python
@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: str
    source_url: str
    text: str
    score: float        # コサイン類似度 [-1.0, 1.0]
```

| 属性 | 説明 |
| --- | --- |
| `chunk_id` | 一致チャンク ID |
| `source_url` | 出典 URL（引用提示用） |
| `text` | チャンク本文（プロンプト注入用） |
| `score` | コサイン類似度（降順ソートキー） |

---

## 永続化マッピング

| エンティティ | ストア | キー |
| --- | --- | --- |
| `ContentChunk`（ハッシュのみ） | ContentDiff (DynamoDB) | PK `chunkId`、GSI `gsi_sourceUrl` |
| `ContentChunk`（本文 + embedding） | VectorStore (DynamoDB) | PK `chunkId`、GSI `gsi_sourceUrl` |
| `ContentChunk`（本文テキスト） | S3 | `content/{url_hash}/{chunk_id}.txt` |
| `embedding` | VectorStore 属性 | `list[Decimal]`（1024 次元） |
