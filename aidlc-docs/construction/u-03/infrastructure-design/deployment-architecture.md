# U-03 AI Conversation Engine — Deployment Architecture

U-03 のデプロイ構成と実行時アーキテクチャ（Connect 連携）。

---

## 1. スタック関係

```
AuJibunBank-{env}-SharedInfra      (U-01)  ──exports SSM──┐
AuJibunBank-{env}-KnowledgePipeline(U-02)  ──VectorStore──┤
AuJibunBank-{env}-Conversation     (U-03)  ←──────────────┘
```

- U-03 `ConversationStack` は U-01 の SSM パラメータ（テーブル名・CMK・権限境界）に依存。
- U-02 が満たす VectorStore コーパスを U-03 RagHandler が読み取る。
- `infra/bin/app.ts` で `dev | staging | prod` を context（`--context env=...`）で切替。

---

## 2. 実行時シーケンス（音声 / チャット）

```
顧客 ─電話/チャット→ Amazon Connect コンタクトフロー
   │ (1) Lambda ブロック invoke (8s 制約)
   ▼
RagHandlerLambda
   │ PII マスク → 文脈構築 → embed → 検索 → 生成 → 履歴保存 (6s 予算)
   ▼
   返却 {answer, sources, hit}
   │
   ├─ hit=True  → Connect が answer を読み上げ/表示
   └─ hit=False → Connect が EscalationLambda を invoke → 有人キュー転送
```

- 会話終了時、コンタクトフローが CsatHandlerLambda（アンケート 1〜5）と要約保存をトリガ。

---

## 3. デプロイ手順（概略）

```bash
# 1. 依存解決とテスト
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run mypy src/

# 2. CDK 合成・デプロイ（infra/）
npm ci
npx cdk synth  --context env=dev
npx cdk deploy AuJibunBank-dev-Conversation --context env=dev
```

- Lambda コードは `lambda.Code.fromAsset('..')`（リポジトリルート）。CI で `uv` により依存込みパッケージをビルドする前提。

---

## 4. 環境別差異

| 項目 | dev | staging | prod |
| --- | --- | --- | --- |
| CustomerHistory 削除ポリシー | DESTROY（U-01） | RETAIN | RETAIN |
| NAT Gateway | 1（U-01） | 1 | 2 |
| ESCALATION_QUEUE_ARN | テスト用キュー | staging キュー | 本番キュー |

---

## 5. ロールバック / 安全策

- Lambda はバージョン管理可能。問題時は前バージョンへエイリアス切替。
- RagHandler は障害時フォールバック（`hit=False`）を返すため、モデル/依存障害でもコンタクトフローはエスカレーションで継続可能（フェイルセーフ）。
- CustomerHistory は PITR 有効（U-01）。TTL 90 日でデータ最小化。
