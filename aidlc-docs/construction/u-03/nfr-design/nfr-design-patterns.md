# U-03 AI Conversation Engine — NFR Design Patterns
# （指数バックオフ・タイムアウト予算・フォールバック）

U-03 の回復性（resilience）を支える設計パターン。

---

## 1. タイムアウト予算パターン（Timeout Budget）

Connect の 8 秒制約に対し、パイプライン全体を 6 秒でガードする。

```python
import asyncio
from src.common.errors import TimeoutBudgetExceeded

try:
    result = await asyncio.wait_for(
        _rag_pipeline(customer_id, user_input, channel, contact_id),
        timeout=6.0,
    )
except TimeoutError:
    # asyncio.TimeoutError をドメインエラーに正規化し、フォールバックへ
    logger.warning("rag timeout", extra={"code": TimeoutBudgetExceeded.code})
    return {"answer": FALLBACK_ANSWER, "sources": [], "hit": False}
```

- 予算は **コード定数 `PIPELINE_BUDGET_SECONDS = 6.0`** に集約（テストで差し替え可能）。
- Lambda 自体は 30 秒タイムアウト。予算はインフラに依存させない。

---

## 2. フォールバックパターン（Graceful Degradation）

`handler` は Connect に対し**例外を投げない**。すべての失敗を `hit=False` のフォールバック応答へ収束させる。

| 失敗 | キャッチ箇所 | 応答 |
| --- | --- | --- |
| 6 秒超過 | `except TimeoutError` | フォールバック文言、`hit=False` |
| Bedrock/Comprehend/Dynamo 例外 | `except AppError` | フォールバック文言、`hit=False` |
| 使えるヒットなし | パイプライン内分岐 | フォールバック文言、`hit=False` |
| 空入力 | handler 冒頭ガード | フォールバック文言、`hit=False` |

- これにより Connect コンタクトフローは常に有効な属性を受け取り、`hit=False` を見てエスカレーションへ分岐できる（US-1.3）。

---

## 3. 指数バックオフ / リトライ（Retry）

- エラー型に `retryable` 属性を持たせる設計（U-01 `AppError`）。`BedrockThrottledError`（retryable=True）/ `TimeoutBudgetExceeded`（retryable=True）。
- U-03 のオンラインパスでは **6 秒予算内**でしかリトライできないため、handler 層では積極的リトライを行わず即フォールバックする。
- バックオフが有効なのは非同期/バッチ経路（将来の再生成・要約バッチ）。その際は `retryable` を起点に指数バックオフ（例: 0.2s, 0.4s, 0.8s + ジッタ）を適用する設計余地を残す。

---

## 4. 依存注入（Testability）

- `_build_dependencies()` がライブのコラボレータ（PiiMasker / Personalizer / BedrockClient / Searcher / HistoryRepository）を生成。
- テストは `patch.object(handler, "_build_dependencies", return_value=mocks)` で全コラボレータをモック化し、ネットワーク非依存で全分岐を検証する。

---

## 5. PII マスク・イン・デプス（Defense in Depth）

- 入力の最初のステップで必ずマスク（検索/生成/保存/ログの前）。
- バイトオフセットを右→左に適用し、置換後もオフセットが破綻しないようにする。
- マスク後テキストが元の PII 文字列を含まないことを hypothesis PBT で性質検証。
