# U-03 AI Conversation Engine — Business Logic Model

# （RAG 8秒パイプライン・パーソナライズ・フォールバック）

U-03 は Amazon Connect コンタクトフローから呼び出され、音声（US-1.1）/チャット（US-1.2）の問い合わせに対し RAG で回答を生成する。Connect の Lambda ブロックには 8 秒の応答制約があるため、パイプライン全体を **6 秒の時間予算**でガードする。

---

## 1. RAG パイプライン（RagHandlerLambda）

```
Connect event
   │  customerId, userInput, channel, contactId
   ▼
① PII マスク        PiiMasker.mask(userInput)         → masked_input, entities
   ▼
② パーソナライズ    Personalizer.build_context(custId)→ history_text
   ▼
③ ベクトル化        BedrockClient.embed(masked_input) → query_vec (Titan v2, 1024d)
   ▼
④ ベクトル検索      CosineSimilaritySearcher.search(  → hits[≤5] (SearchHit)
                       query_vec, top_k=5)
   ▼
⑤ 回答生成          BedrockClient.generate_answer(    → answer, source_urls
                       masked_input, chunks, history)   (Claude Sonnet 4.6)
   ▼
⑥ 履歴保存          HistoryRepository.append_turn x2  (user + assistant)
   ▼
return {"answer", "sources", "hit": True}
```

- パイプライン全体は `asyncio.wait_for(_rag_pipeline(...), timeout=6.0)` でラップする。
- ③→④→⑤ は逐次依存（前段の出力が後段の入力）のため直列実行。

---

## 2. ヒット判定とフォールバック

| 状況 | 判定 | 応答 |
| --- | --- | --- |
| 検索ヒットあり（score ≥ 0.3） | `hit=True` | 生成回答 + sources |
| 使えるヒットなし | `hit=False` | フォールバック文言、sources=[] |
| 6 秒予算超過（`asyncio.TimeoutError`） | `hit=False` | フォールバック文言（raise しない） |
| Bedrock / Comprehend 例外（`AppError`） | `hit=False` | フォールバック文言（raise しない） |

- `handler` は Connect に対して **例外を投げない**。すべての失敗をフォールバック応答に収束させ、コンタクトフロー側が `hit=False` を見てエスカレーション（US-1.3）へ分岐できるようにする。
- フォールバック文言: 「申し訳ございません。ただいまお答えをご用意できませんでした。オペレーターにおつなぎいたします。」

---

## 3. パーソナライズ（US-6.2）

- `Personalizer.build_context(customerId, limit=5)` が CustomerHistory から直近 5 ターンを取得し、`顧客: ...` / `エージェント: ...` 形式のテキストに整形する。
- `customerId == "anonymous"` または未識別の場合は空文字を返す（履歴参照なし）。
- 整形済みテキストは generate_answer のプロンプト「# 過去の会話」セクションに渡される。

---

## 4. エスカレーション（US-1.3）

- RAG が `hit=False` を返した場合、コンタクトフローは EscalationLambda を呼び出す。
- EscalationLambda は有人キューへの転送属性 `{"escalate": True, "queue_arn", "reason"}` を返す。
- `reason` 既定値は `no_knowledge_match`。

---

## 5. 履歴・サマリ・CSAT（US-1.4 / US-6.1）

| 操作 | SK 形式 | トリガ |
| --- | --- | --- |
| ターン追記 | `TURN#<ISO8601>` | RAG 各ターン |
| 会話サマリ保存 | `SUMMARY#<contactId>` | コンタクト終了 |
| CSAT 保存 | `CSAT#<contactId>` | アンケート完了（1〜5） |

- 全 CustomerHistory 項目は TTL（`expiresAt` = now + 90 日）を設定。
- 永続化する `text` はすべて PII マスク済み。
