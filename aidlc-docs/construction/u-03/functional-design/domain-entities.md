# U-03 AI Conversation Engine — Domain Entities
# （ConversationTurn・RagAnswer・SessionContext）

U-03 の主要ドメインエンティティと、その永続化形式（CustomerHistory テーブル）を定義する。

---

## 1. ConversationTurn

会話の 1 ターン。`src/session_manager/history.py` に `@dataclass(frozen=True)` として定義。

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `role` | `str` | `"user"` または `"assistant"` |
| `text` | `str` | ターン本文（**PII マスク済み**） |
| `timestamp` | `str` | ISO8601 タイムスタンプ |
| `contact_id` | `str` | Amazon Connect コンタクト ID |
| `channel` | `str` | `"voice"` または `"chat"` |

### CustomerHistory への永続化（ターン）

| 属性 | 値 |
| --- | --- |
| `customerId`（PK） | 顧客 ID |
| `sk`（SK） | `TURN#<timestamp>` |
| `role` / `text` / `timestamp` / `contactId` / `channel` | 上記フィールド |
| `expiresAt` | now + 90 日（epoch 秒、TTL） |

---

## 2. RagAnswer

RAG パイプラインの出力。Connect への戻り値（`dict`）として表現。

| キー | 型 | 説明 |
| --- | --- | --- |
| `answer` | `str` | 生成回答、またはフォールバック文言 |
| `sources` | `list[str]` | 回答根拠の重複排除済みソース URL |
| `hit` | `bool` | 使えるヒットがあり回答生成できたか |

- `BedrockClient.generate_answer()` は `tuple[str, list[str]]`（answer, source_urls）を返し、handler がこれを `RagAnswer` 形に整形する。

---

## 3. SessionContext（パーソナライズ文脈）

`Personalizer.build_context()` が生成する文字列。永続エンティティではなく派生値。

```
顧客: <masked text>
エージェント: <answer>
顧客: ...
```

- 直近 `limit`（既定 5）ターンを **古い順**に整形。
- 匿名（`customerId == "anonymous"`）または履歴なしの場合は空文字。

---

## 4. SearchHit（U-02 から再利用）

`src/vector_store/searcher.py` の `SearchHit`（`frozen, slots`）。

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `chunk_id` | `str` | チャンク ID |
| `source_url` | `str` | 出典ページ URL |
| `text` | `str` | チャンク本文（RAG コンテキスト） |
| `score` | `float` | コサイン類似度 |

- handler は `score ≥ MIN_HIT_SCORE` のヒットのみを `{"text", "source_url"}` 形に変換して generate_answer に渡す。

---

## 5. 補助エンティティ（CustomerHistory 共用）

| エンティティ | SK 形式 | 主属性 |
| --- | --- | --- |
| Summary | `SUMMARY#<contactId>` | `summary`, `contactId`, `expiresAt` |
| Csat | `CSAT#<contactId>` | `score`(1-5), `contactId`, `expiresAt` |

- すべて PK=`customerId`、TTL=`expiresAt`（90 日）。`gsi_contactId`（U-01 定義）で contactId 横断検索が可能。
