# RAG 品質・レイテンシ評価ハーネス (Phase C/D)

デプロイ済み chat-api（U-08 Function URL, SSE）に固定の質問セットを投げ、
**回答品質**と**ストリーミングレイテンシ**を一度に計測します。

## 実行

```bash
export CHAT_ENDPOINT=https://<id>.lambda-url.ap-northeast-1.on.aws
export DEMO_KEY=$(aws secretsmanager get-secret-value \
  --secret-id au-jibun-bank-dev-chat-demo-key --query SecretString --output text)
uv run python scripts/rag_eval/evaluate.py
```

## 計測指標

- **hit-rate** — fallback ではなく根拠ある回答を返した割合
- **source-grounded** — 回答に jibunbank.co.jp のソースが1件以上付いた割合
- **negative control** — 無意味な入力（`expect_miss`）を正しく「わかりかねます」で拒否した割合（ハルシネーション検査）
- **TTFT** — 最初のトークン到達時間（ユーザーが体感するレイテンシ）。ストリーミングの主効果
- **total** — 最終トークンまでの総時間

## 参考結果（2026-06-28, dev・キャッシュ再ビルド後）

```text
answerable hit-rate : 100% (14/14)
source-grounded     : 100% (>=1 jibunbank source)
negative control    : 100% declined as expected
TTFT  p50/p95       : 1480ms / 22884ms   ← p95 は初回コールド1件
total p50/p95       : 4183ms / 25765ms
total mean          : 5672ms
```

ウォーム時 **TTFT 約1.5秒**。非ストリーミング（一括）なら体感は総時間 ~4秒だが、
ストリーミングにより最初のトークンが ~1.5秒で出始める。

## 質問セット

`questions.json` を編集（`id` / `question` / `keywords` / 任意の `expect_miss`）。
`keywords` は回答に含まれるべき語、`expect_miss:true` は拒否されるべき負例。
