# U-03 AI Conversation Engine — NFR Requirements

U-03 の非機能要件。各要件は測定可能な基準と検証手段を持つ。

---

## 1. 性能（Performance）

| ID | 要件 | 基準 |
| --- | --- | --- |
| NFR-P1 | Connect への応答時間 | 8 秒以内（ハード制約） |
| NFR-P2 | RAG パイプライン時間予算 | 6 秒（`asyncio.wait_for`） |
| NFR-P3 | ベクトル検索キャッシュ | /tmp warm cache（U-02、TTL 900 秒）でコールド以外は DynamoDB スキャン回避 |
| NFR-P4 | 検索 top_k | 5 件（コンテキスト過多による生成遅延を抑制） |
| NFR-P5 | Lambda メモリ | RagHandler 512MB、補助 Lambda 256MB |

---

## 2. 信頼性・可用性（Reliability）

| ID | 要件 | 基準 |
| --- | --- | --- |
| NFR-R1 | フォールバック | タイムアウト/モデル障害時にフォールバック応答を返し、例外を Connect に伝播しない |
| NFR-R2 | スロットリング耐性 | `BedrockThrottledError`（retryable=True）を型で識別可能。再試行は時間予算内 |
| NFR-R3 | 冪等性 | 履歴追記は SK=`TURN#<timestamp>` により実質的に一意 |
| NFR-R4 | 障害分離 | 履歴保存失敗は回答生成後に発生し得るが、回答自体は返す設計を志向 |

---

## 3. セキュリティ・プライバシー（Security）

| ID | 要件 | 基準 |
| --- | --- | --- |
| NFR-S1 | PII マスク | 入力は検索/生成/保存/ログ前に必ず Comprehend でマスク |
| NFR-S2 | 保存時暗号化 | CustomerHistory は U-01 の共用 CMK で暗号化 |
| NFR-S3 | 最小権限 IAM | テーブル/モデル ARN 単位。`"*"` は Comprehend Detect* と AWS 要件のみ |
| NFR-S4 | 認証情報 | ハードコード禁止。実行ロール/環境変数経由 |
| NFR-S5 | TTL | 履歴は 90 日で自動失効（データ最小化） |

---

## 4. 観測性（Observability）

| ID | 要件 | 基準 |
| --- | --- | --- |
| NFR-O1 | 構造化ログ | AWS Lambda Powertools `Logger`（JSON）。PII を含めない |
| NFR-O2 | エラーアラーム | RagHandler の Errors / Throttles に CloudWatch アラーム |
| NFR-O3 | ログ保持 | 90 日（U-01 共通） |

---

## 5. 保守性・テスト容易性（Maintainability）

| ID | 要件 | 基準 |
| --- | --- | --- |
| NFR-M1 | 型安全 | mypy strict 合格（Python 3.12 型スタイル） |
| NFR-M2 | Lint | ruff（E/F/I/UP/B/C4/SIM/RUF）合格 |
| NFR-M3 | テスト | moto + unittest.mock + hypothesis。各テスト独立実行可能 |
| NFR-M4 | 依存注入 | `_build_dependencies()` でコラボレータを差し替え可能にしテスト容易性を確保 |
