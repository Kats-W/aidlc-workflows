# U-02 Deployment Architecture — Knowledge Pipeline

---

## 1. スタック関係

```
AuJibunBank-{env}-SharedInfra   (U-01)
        │  exports → SSM Parameter Store
        ▼
AuJibunBank-{env}-KnowledgePipeline   (U-02)
   ├─ CrawlerLambda  + CrawlerRole
   ├─ EmbedderLambda + EmbedderRole
   ├─ WeeklyCrawlSchedule (EventBridge Scheduler) + ScheduleRole
   └─ CloudWatch Alarms (Crawler / Embedder)
```

- U-02 は U-01 に **SSM 経由で疎結合**。CloudFormation の硬直的な Export/ImportValue を避ける。
- デプロイ順序: SharedInfra → KnowledgePipeline。

---

## 2. ランタイムデータフロー

```
Scheduler ──(Sun 02:00 JST)──▶ CrawlerLambda
                                   │ httpx GET (robots 遵守)
                                   ▼
                          公式サイト / FAQ
                                   │ parse + chunk
                                   ├─▶ S3 (content/...txt)
                                   ├─▶ ContentDiff (diff + commit)
                                   └─(Event Invoke)─▶ EmbedderLambda
                                                          │ Titan v2 embed
                                                          └─▶ VectorStore (upsert/delete)

[検索パス] U-03 ChatLambda ─▶ CosineSimilaritySearcher
                                   ├─ /tmp cache (.npy + JSON, TTL 900s)
                                   └─ miss → VectorStore.scan_all()
```

---

## 3. 環境差分

| 項目 | dev | staging / prod |
| --- | --- | --- |
| ログ保持 | 90 日 | 90 日 |
| Scheduler | 有効 | 有効 |
| ターゲット URL | bin/app.ts の既定リスト | 同（必要に応じ context で上書き） |
| 権限境界 | SharedInfra の境界を継承 | 同 |

---

## 4. デプロイ手順

```bash
# Python パッケージ（uv）でテスト・型チェック
uv run pytest tests/unit/crawler tests/unit/vector_store tests/unit/common
uv run mypy src

# CDK
cd infra
npm ci
npm run synth -- --context env=dev
npm run deploy:dev   # SharedInfra デプロイ済み前提
```

---

## 5. ロールバック / 運用

- VectorStore / ContentDiff は SharedInfra 管理（PITR 有効）。U-02 スタック削除でデータは失われない（dev 以外 RETAIN）。
- クロール失敗は CloudWatch アラームで検知し、手動 / Scheduler の次回実行で回復。
- /tmp キャッシュは Lambda コンテナ寿命に依存し、TTL 経過後に自動的に再構築される。
