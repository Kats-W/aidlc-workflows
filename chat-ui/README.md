# AI カスタマーサポート チャット UI (U-08)

Connect 音声経路の顧客向け Web 版。U-08 chat-api（Lambda Function URL, SSE
ストリーミング）に接続し、RAG の回答をトークン逐次で表示するデモ用チャット
SPA です。

> ⚠️ 非公式の技術デモです。実在の金融機関とは一切関係がありません。詳細は
> リポジトリルートの [PROJECT.md](../PROJECT.md) の免責事項を参照してください。

## セットアップ

```bash
cd chat-ui
npm install
cp .env.example .env.local
# .env.local を編集（ChatStack の出力から取得）
npm run dev   # http://localhost:5174
```

## 環境変数

- `VITE_CHAT_ENDPOINT` — ChatStack の CfnOutput `ChatApiUrl`（Function URL）
- `VITE_DEMO_KEY` — `aws secretsmanager get-secret-value --secret-id au-jibun-bank-dev-chat-demo-key --query SecretString --output text` で取得

## 仕組み

`src/api/chatClient.ts` が `POST {VITE_CHAT_ENDPOINT}/chat` に
`{ message, sessionId }` を送り、`fetch` + `ReadableStream` で SSE
（`sources` → `token*` → `done` / `error`）を読み取り、`App.tsx` が
吹き出しにトークンを逐次追記・参照元リンクを表示します。
