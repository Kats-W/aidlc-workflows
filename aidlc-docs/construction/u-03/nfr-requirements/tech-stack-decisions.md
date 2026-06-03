# U-03 AI Conversation Engine — Tech Stack Decisions

U-03 で採用した技術と選定根拠。U-01/U-02 の既存スタックと整合させる。

---

## 1. 回答生成モデル: Claude Sonnet 4.6（Bedrock）

- モデル ID: `anthropic.claude-sonnet-4-6-20250514-v1:0`、`anthropic_version="bedrock-2023-05-31"`。
- 根拠: 日本語の丁寧な応答品質と 8 秒制約に収まる応答速度のバランス。Messages API でプロンプト構造（過去会話＋参考情報＋質問）を明確に表現できる。
- 代替検討: Opus は品質高だが latency が 6 秒予算に対しリスク。Haiku は速いが日本語ニュアンス劣後。→ Sonnet 採用。

---

## 2. ベクトル化: Amazon Titan Text Embeddings v2

- モデル ID: `amazon.titan-embed-text-v2:0`、1024 次元、`normalize=true`。
- 根拠: U-02 の埋め込みと**同一モデル/次元**でなければコサイン検索が成立しないため固定。既存 `BedrockClient.embed()` を再利用。

---

## 3. PII 検出: Amazon Comprehend `DetectPiiEntities`

- 根拠: マネージドで日本語（`ja`）対応。バイトオフセットで正確なスパン置換が可能。
- 制約: 1 リクエスト 100KB（UTF-8）上限。IAM はリソースレベル不可のため `Resource: "*"`（API 制約）。

---

## 4. ベクトル検索: 自前コサイン類似度（U-02 再利用）

- `CosineSimilaritySearcher`（numpy、/tmp に npy+JSON キャッシュ、**pickle 不使用**）。
- 根拠: コーパス規模が小〜中で OpenSearch 等の常時稼働コスト不要。U-02 実装を流用。

---

## 5. 履歴ストア: DynamoDB CustomerHistory（U-01 提供）

- PK=`customerId`、SK=`sk`、TTL=`expiresAt`、`gsi_contactId`。
- 根拠: 低レイテンシ・サーバレス・TTL による自動失効（データ最小化）。On-Demand 課金。

---

## 6. 実行基盤: AWS Lambda（Python 3.12）+ Amazon Connect

- RagHandler 512MB/30s、補助 3 Lambda 256MB/10s。
- 8 秒制約はインフラではなく**コード側の `asyncio.wait_for(6.0)`** で制御（環境差異に強い）。
- boto3 同期クライアントは `asyncio.to_thread` でラップ（**同期 `requests` 禁止**を遵守）。

---

## 7. 横断ライブラリ

| 用途 | ライブラリ | 備考 |
| --- | --- | --- |
| ログ | aws-lambda-powertools `Logger` | 構造化 JSON |
| 数値計算 | numpy | コサイン類似度 |
| AWS SDK | boto3（同期）+ `asyncio.to_thread` | aioboto3 は将来検討余地 |
| テスト | pytest / pytest-asyncio / moto / hypothesis | PBT で TTL・マスク性質を検証 |
| 型/Lint | mypy strict / ruff | Python 3.12 型スタイル |
