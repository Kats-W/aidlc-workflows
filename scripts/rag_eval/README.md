# RAG 品質・レイテンシ評価ハーネス (Phase C/D)

回答品質を「根拠つき回答を返したか」だけでなく、**忠実性（ハルシネーションの無さ）**と
**有用性（実際に役立つか）**で測り、レイテンシ（ストリーミング体感）も計測します。

## スクリプト

- **`judge_eval.py`** — ローカルでパイプライン全体（embed→search→generate）を実行し、
  **LLM-as-judge（Claude Sonnet）**が検索文脈に対して忠実性/有用性を 1–5 で採点。
  「根拠あり」と「実際に良い」を区別し、コーパス欠落（hedge）を炙り出す。
- **`inspect_retrieval.py`** — ある質問に対し検索が返す上位チャンク（スコア・出典・本文）を
  表示。弱い回答が「検索ミス」か「コーパス欠落」か「生成の問題」かを切り分ける診断用。
- **`evaluate.py`** — デプロイ済み chat-api（Function URL, SSE）に投げ、ヒット率・
  ソース根拠・**TTFT/総時間**を計測（レイテンシ実測 / Phase D）。

## 実行

```bash
export VECTOR_STORE_TABLE_NAME=au-jibun-bank-dev-vector-store
export CRAWL_CONTENT_BUCKET=au-jibun-bank-dev-crawl-content-568115736711
export AWS_REGION=ap-northeast-1

uv run python scripts/rag_eval/judge_eval.py
uv run python scripts/rag_eval/inspect_retrieval.py "住宅ローンの金利を教えて" 8

# evaluate.py はデプロイ済みエンドポイントに対して実行
export CHAT_ENDPOINT=https://<id>.lambda-url.ap-northeast-1.on.aws
export DEMO_KEY=$(aws secretsmanager get-secret-value \
  --secret-id au-jibun-bank-dev-chat-demo-key --query SecretString --output text)
uv run python scripts/rag_eval/evaluate.py
```

## 採点指標（judge_eval）

- **faithfulness（忠実性, 1–5）** — 回答の各主張が検索文脈で裏付けられるか（捏造の検出）
- **usefulness（有用性, 1–5）** — 質問に実際に役立つ回答ができているか
- **verdict** — good / hedge（忠実だが情報不足）/ hallucination / miss

## 結果（dev, 2026-06-29, 14 問）

検索診断で「無関係文脈が低スコアで混入し誤対応付け／捏造を誘発」と判明し、
**検索閾値（0.30→0.40）とプロンプト**を調整して改善:

```text
              忠実性   有用性   的確(≥4/≥4)  ハルシネーション
調整前(0.30)   4.14     3.79     57%          3 件
調整後(0.40)   4.71     3.57     71%          0 件
```

残り 29% は捏造ではなく**コーパス欠落**（振込手数料・デビット還元・現在の定期金利など）で、
システムは無理に答えず安全に hedge する。次の改善は headless クロールでの取得改善。

レイテンシ（evaluate.py）: ウォーム **TTFT 中央値 ~1.5 秒** / 総時間 ~4.2 秒。

## 質問セット

`questions.json` を編集（`id` / `question` / `keywords` / 任意の `expect_miss`）。
