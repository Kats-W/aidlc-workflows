# U-03 AI Conversation Engine — Business Rules
# （8秒制約・PII 必須マスク・エスカレーション条件）

U-03 が強制するビジネスルール。U-01 のセキュリティルールおよび Security Extension のブロッキングルールと整合する。

---

## 1. 応答時間ルール（8 秒制約）

| ルール | 内容 |
| --- | --- |
| BR-1.1 | Connect Lambda ブロックの応答は **8 秒以内**。RAG パイプラインは **6 秒の時間予算**でガードする（`asyncio.wait_for(timeout=6.0)`）。 |
| BR-1.2 | 6 秒を超過した場合は `TimeoutBudgetExceeded` 相当として扱い、フォールバック応答を返す（例外を Connect に伝播しない）。 |
| BR-1.3 | Lambda 自体のタイムアウトは 30 秒に設定し、6 秒予算は **コード側**で制御する（インフラ依存にしない）。 |
| BR-1.4 | 余裕 2 秒は Connect 側の往復・コールドスタート余地として確保する。 |

---

## 2. PII 必須マスクルール

| ルール | 内容 |
| --- | --- |
| BR-2.1 | ユーザー入力は **ベクトル化・検索・LLM 送信・履歴保存・ログ出力の前に必ず** `PiiMasker.mask()` を通す。 |
| BR-2.2 | 検出された PII スパンはすべて `[MASKED]` トークンに置換する。 |
| BR-2.3 | CustomerHistory に保存する `text` は PII マスク済みのみ（生の PII を保存しない）。 |
| BR-2.4 | CloudWatch Logs に PII を出力しない。`logger` の `extra` にはマスク済み/メタ情報のみ。 |
| BR-2.5 | Comprehend のリクエスト上限（100KB / UTF-8）超過時は `ComprehendError` を送出する。 |

---

## 3. エスカレーション条件（US-1.3）

| ルール | 内容 |
| --- | --- |
| BR-3.1 | ベクトル検索の最大スコアが閾値（`MIN_HIT_SCORE = 0.3`）未満なら `hit=False`。 |
| BR-3.2 | `hit=False`、6 秒超過、Bedrock/Comprehend エラーのいずれでもフォールバック応答（`hit=False`）。 |
| BR-3.3 | コンタクトフローは `hit=False` を検知し、EscalationLambda 経由で有人キューへ転送する。 |
| BR-3.4 | エスカレーション応答は `{"escalate": True, "queue_arn", "reason"}`。`reason` 既定 `no_knowledge_match`。 |

---

## 4. CSAT ルール（US-1.4）

| ルール | 内容 |
| --- | --- |
| BR-4.1 | CSAT スコアは整数 **1〜5**。範囲外（0, 6, 負値等）は `ValidationError`。 |
| BR-4.2 | `customerId` と `contactId` は必須。欠落時は `ValidationError`。 |
| BR-4.3 | SK は `CSAT#<contactId>`、TTL 90 日。 |

---

## 5. 履歴・パーソナライズルール（US-6.1 / US-6.2）

| ルール | 内容 |
| --- | --- |
| BR-5.1 | CustomerHistory の全項目に TTL（`expiresAt` = now + 90 日）を設定。 |
| BR-5.2 | パーソナライズは直近 5 ターンまで。`customerId == "anonymous"` は履歴参照しない。 |
| BR-5.3 | ターン SK は `TURN#<ISO8601 timestamp>`、降順クエリ（`ScanIndexForward=False`）で最新順に取得。 |

---

## 6. モデル・権限ルール

| ルール | 内容 |
| --- | --- |
| BR-6.1 | 回答生成は Claude Sonnet 4.6（`anthropic.claude-sonnet-4-6-20250514-v1:0`、`anthropic_version="bedrock-2023-05-31"`）。 |
| BR-6.2 | ベクトル化は Titan Text Embeddings v2（1024 次元、normalize=true）。 |
| BR-6.3 | IAM は最小権限。`bedrock:InvokeModel` は使用する foundation-model ARN のみ。`comprehend:DetectPiiEntities` は API 制約上 `Resource: "*"`。 |
| BR-6.4 | 認証情報のハードコード禁止。実行ロール / 環境変数経由のみ。 |
