# U-04 Omnichannel & Escalation — Domain Entities

# （SessionContext・SESSION# スキーマ・ConversationTurn 再利用）

U-04 の主要ドメインエンティティと CustomerHistory への永続化形式を定義する。

---

## 1. SessionContext

セッション中の文脈。`src/session_manager/channel_switch.py` に `@dataclass(frozen=True)` として定義。

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `contact_id` | `str` | Amazon Connect コンタクト ID（PK と一致） |
| `turns` | `list[ConversationTurn]` | 会話ターン（古い順、最大 20） |
| `channel` | `str` | 現在のチャネル `"voice"` または `"chat"` |
| `summary` | `str \| None` | 事前計算済みサマリー、無ければ `None` |

---

## 2. ConversationTurn（U-03 から再利用）

`src/session_manager/history.py` の `@dataclass(frozen=True)` をインポートして使用。

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `role` | `str` | `"user"` または `"assistant"` |
| `text` | `str` | ターン本文（**PII マスク済み**） |
| `timestamp` | `str` | ISO8601 タイムスタンプ |
| `contact_id` | `str` | コンタクト ID |
| `channel` | `str` | `"voice"` または `"chat"` |

```python
from src.session_manager.history import ConversationTurn
```

---

## 3. CustomerHistory への永続化（SESSION# エントリ）

| 属性 | 値 |
| --- | --- |
| `customerId`（PK） | `contactId`（匿名は `ANON#<contactId>`） |
| `sk`（SK） | `SESSION#<contactId>` |
| `turns` | L — `ConversationTurn` の JSON 互換マップのリスト（最大 20） |
| `channel` | S — 直近ターンのチャネル |
| `updatedAt` | S — ISO8601 更新時刻 |
| `expiresAt` | N — now + 90 日（epoch 秒、TTL） |

### ターンのマップ表現（`turns` リストの要素）

| キー | 値 |
| --- | --- |
| `role` / `text` / `timestamp` | 上記フィールド |
| `contactId` | `ConversationTurn.contact_id` |
| `channel` | `ConversationTurn.channel` |

- `pickle` は使用せず、JSON 互換の Map/List 型で永続化する。

---

## 4. handler の入出力

### 入力イベント

| キー | 型 | 説明 |
| --- | --- | --- |
| `contactId` | `str` | 必須。コンタクト ID |
| `channelFrom` | `str` | 切り替え元チャネル |
| `channelTo` | `str` | 切り替え先チャネル |
| `lastN` | `int` | 任意。サマリー対象ターン数（既定 5） |

### 戻り値

| キー | 型 | 説明 |
| --- | --- | --- |
| `handover_summary` | `str` | 引き継ぎサマリー（新規時は空文字） |
| `channel_from` | `str` | 切り替え元 |
| `channel_to` | `str` | 切り替え先 |
| `turn_count` | `int` | 引き継いだターン総数 |

---

## 5. 補助エンティティ（CustomerHistory 共用）

`TURN#` / `SUMMARY#` / `CSAT#`（U-01〜U-03）と同一テーブルを共用。
`SESSION#` は U-04 で追加する SK プレフィックス。すべて PK=`customerId`、TTL=`expiresAt`（90 日）。
