# U-03 AI Conversation Engine — Logical Components

U-03 の論理コンポーネントと責務・依存関係。

---

## 1. コンポーネント一覧

| コンポーネント | モジュール | 責務 |
| --- | --- | --- |
| RagHandler | `src/rag_handler/handler.py` | Connect フック。6 秒予算でパイプライン統制、フォールバック |
| Personalizer | `src/rag_handler/personalizer.py` | 直近ターンから会話文脈を構築（US-6.2） |
| Escalation | `src/rag_handler/escalation.py` | 有人キュー転送属性を返す（US-1.3） |
| PiiMasker | `src/common/pii_masker.py` | Comprehend で PII 検出→`[MASKED]` 置換 |
| BedrockClient | `src/common/bedrock_client.py` | `embed` / `generate_answer` / `analyze_gap` |
| CosineSimilaritySearcher | `src/vector_store/searcher.py`（U-02） | コサイン top-k 検索 |
| HistoryRepository | `src/session_manager/history.py` | ターン追記・取得・サマリ保存 |
| CsatHandler | `src/session_manager/csat_handler.py` | CSAT スコア保存（US-1.4） |

---

## 2. 依存関係

```
RagHandler
 ├─ PiiMasker            ─→ Comprehend
 ├─ Personalizer         ─→ HistoryRepository ─→ DynamoDB(CustomerHistory)
 ├─ BedrockClient.embed  ─→ Bedrock(Titan v2)
 ├─ CosineSimilaritySearcher ─→ VectorStore ─→ DynamoDB(VectorStore)
 ├─ BedrockClient.generate_answer ─→ Bedrock(Claude Sonnet 4.6)
 └─ HistoryRepository.append_turn ─→ DynamoDB(CustomerHistory)

Escalation     ─→ (環境変数 ESCALATION_QUEUE_ARN のみ、データアクセスなし)
CsatHandler    ─→ DynamoDB(CustomerHistory)
```

- すべての boto3 同期呼び出しは `asyncio.to_thread` でラップし、async インタフェースを維持。

---

## 3. データフロー境界

| 境界 | データ | マスク状態 |
| --- | --- | --- |
| Connect → RagHandler | `userInput`（生） | 未マスク |
| RagHandler 内（マスク後） | `masked_input` | マスク済み |
| RagHandler → Bedrock/Searcher/History | `masked_input`, `answer` | マスク済み |
| ログ出力 | メタ情報のみ | PII 非出力 |

- 生 PII は PiiMasker を通過する瞬間まで。以降の全ステップ・永続化・ログはマスク済みのみ。

---

## 4. コンポーネント別 IAM（最小権限）

| Lambda | 必要権限 |
| --- | --- |
| RagHandler | VectorStore(read) / CustomerHistory(read+PutItem) / Bedrock InvokeModel(Titan+Claude) / Comprehend DetectPiiEntities / KMS / SSM(read) |
| Personalizer | CustomerHistory(read) / KMS / SSM(read) |
| Escalation | 基本実行ロールのみ（データアクセスなし） |
| CsatHandler | CustomerHistory(PutItem) / KMS / SSM(read) |

- `bedrock:InvokeModel` は使用モデル ARN に限定。`comprehend:DetectPiiEntities` は API 制約で `Resource:"*"`。
- 全ロールに U-01 の権限境界（Permission Boundary）を付与。
