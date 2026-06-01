# Technical Environment Document — au Jibun Bank AI Agent

## Language & Runtime

- **Primary language**: Python 3.12
- **IaC language**: TypeScript (AWS CDK v2)
- **Package manager**: uv (Python), npm (CDK/Node)
- **Python dependencies**: managed via `pyproject.toml` + `uv.lock`

---

## AWS Services (Core Stack)

| Service | Role |
|---|---|
| Amazon Connect | オムニチャネルコンタクトセンター基盤（音声・チャット） |
| Amazon Lex v2 | 日本語 NLU / ダイアログ管理 |
| Amazon Bedrock (Claude claude-sonnet-4-6) | RAG 回答生成・ナレッジギャップ分析・改善提案生成 |
| Amazon Bedrock Knowledge Bases | ベクトルストア管理（OpenSearch Serverless backend） |
| Amazon S3 | クローリング済みコンテンツ保存・Bedrock データソース |
| AWS Lambda (Python 3.12) | ビジネスロジック全般（クローラー、RAG ハンドラー、改善提案生成） |
| Amazon EventBridge Scheduler | 日次クローリング・週次改善提案生成のスケジューリング |
| Amazon DynamoDB | 顧客会話履歴・改善提案・クローリング差分管理 |
| Amazon CloudWatch | ログ・メトリクス・ダッシュボード |
| Amazon Connect Contact Lens | 会話品質分析・感情分析 |
| Amazon Comprehend | 会話テキスト内 PII 検出・マスク処理 |
| AWS Secrets Manager | API キー・クレデンシャル管理 |
| AWS IAM | 最小権限ロール管理 |
| Amazon SNS / SES | 改善提案承認通知（メール） |

---

## AWS Region

- **Primary**: `ap-northeast-1`（東京）
- Amazon Connect はこのリージョンでインスタンスを作成済みと仮定（既存インスタンス ID は環境変数で注入）

---

## Deployment Model

- **IaC**: AWS CDK v2 (TypeScript)
- **CI/CD**: GitHub Actions → CDK deploy
- **Environment staging**: `dev` / `staging` / `prod`（CDK context で制御）
- **Lambda パッケージング**: Python Lambda layers + CDK bundling（Docker）

---

## Project Directory Structure

```
au-jibun-bank-ai-agent/
├── cdk/                          # CDK スタック定義
│   ├── bin/
│   │   └── app.ts
│   └── lib/
│       ├── stacks/
│       │   ├── connect-stack.ts      # Amazon Connect + Lex
│       │   ├── knowledge-stack.ts    # Bedrock KB + S3
│       │   ├── agent-stack.ts        # Lambda functions
│       │   ├── storage-stack.ts      # DynamoDB tables
│       │   └── dashboard-stack.ts    # CloudWatch dashboard
│       └── constructs/
├── src/
│   ├── crawler/                  # ウェブクローラー Lambda
│   │   ├── handler.py
│   │   ├── parser.py
│   │   └── differ.py
│   ├── rag_handler/              # Amazon Connect Lambda hook
│   │   ├── handler.py
│   │   ├── retriever.py
│   │   └── personalizer.py
│   ├── improvement_generator/    # 週次改善提案生成 Lambda
│   │   ├── handler.py
│   │   └── analyzer.py
│   ├── session_manager/          # セッション継続・履歴 Lambda
│   │   ├── handler.py
│   │   └── history.py
│   └── common/
│       ├── bedrock_client.py
│       ├── dynamo_client.py
│       └── pii_masker.py
├── tests/
│   ├── unit/
│   └── integration/
├── aidlc-docs/                   # AI-DLC 生成ドキュメント（このリポジトリ内）
└── pyproject.toml
```

---

## Prohibited Libraries / Patterns

| 禁止事項 | 理由 | 代替案 |
|---|---|---|
| `scrapy` | Lambda サイズ制限超過リスク・オーバースペック | `httpx` + `beautifulsoup4` |
| `requests` (sync) | Lambda の async コンテキストでの非効率 | `httpx` (async) |
| `openai` SDK | AWS Bedrock を使用するため不要 | `boto3` Bedrock Runtime |
| ハードコードされた認証情報 | セキュリティリスク | AWS Secrets Manager / 環境変数 |
| `pickle` シリアライズ | セキュリティリスク（任意コード実行） | `json` / `msgpack` |
| グローバルミュータブル状態（Lambda） | コールドスタート間での状態汚染 | クラスインスタンスをハンドラースコープ外で初期化するのみ可 |

---

## Test Framework

- `pytest` + `pytest-asyncio`
- `moto` for AWS service mocking
- `boto3-stubs` for type hints
- Target coverage: 80% on internal code paths（外部 AWS API コール除外）
- Unit tests: `tests/unit/`
- Integration tests（LocalStack または実 AWS dev 環境）: `tests/integration/`

---

## Security Baseline

- Lambda 実行ロールは最小権限（リソース ARN 指定）
- S3 バケット: パブリックアクセスブロック有効・サーバーサイド暗号化（SSE-S3）
- DynamoDB: 保存時暗号化（AWS マネージドキー）
- Amazon Connect コンタクトフロー: TLS 通信のみ
- PII 処理: Amazon Comprehend でマスク後に DynamoDB へ保存
- Secrets Manager でクレデンシャル管理（環境変数への平文記録禁止）
- CloudTrail 有効化（API 監査ログ）

---

## Code Examples

### 典型的な Lambda ハンドラー（RAG）

```python
import json
import boto3
from common.bedrock_client import BedrockClient
from common.dynamo_client import DynamoClient
from common.pii_masker import mask_pii


bedrock = BedrockClient()
dynamo = DynamoClient()


async def handler(event: dict, context) -> dict:
    contact_id = event["Details"]["ContactData"]["ContactId"]
    customer_id = event["Details"]["ContactData"]["Attributes"].get("customerId", "anonymous")
    user_input = event["Details"]["ContactData"]["Attributes"]["userInput"]

    masked_input = await mask_pii(user_input)
    history = await dynamo.get_recent_history(customer_id, limit=5)
    answer = await bedrock.retrieve_and_generate(masked_input, history)

    await dynamo.append_turn(customer_id, contact_id, masked_input, answer)

    return {
        "statusCode": 200,
        "body": json.dumps({"answer": answer}),
    }
```

### 典型的な DynamoDB アクセスパターン

```python
from boto3.dynamodb.conditions import Key


async def get_recent_history(self, customer_id: str, limit: int = 5) -> list[dict]:
    response = self.table.query(
        KeyConditionExpression=Key("customerId").eq(customer_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return response.get("Items", [])
```

### 典型的な Bedrock RAG 呼び出し

```python
async def retrieve_and_generate(self, query: str, history: list[dict]) -> str:
    history_text = "\n".join(
        f"顧客: {t['userInput']}\nエージェント: {t['agentResponse']}"
        for t in reversed(history)
    )
    response = self.bedrock_agent_runtime.retrieve_and_generate(
        input={"text": query},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": self.kb_id,
                "modelArn": f"arn:aws:bedrock:ap-northeast-1::foundation-model/anthropic.claude-sonnet-4-6-20250514-v1:0",
                "generationConfiguration": {
                    "promptTemplate": {
                        "textPromptTemplate": (
                            f"過去の会話:\n{history_text}\n\n"
                            "以下のコンテキストを参考に、au じぶん銀行のカスタマーサポートとして"
                            "丁寧かつ正確に回答してください。\n\n$search_results$\n\n質問: $query$"
                        )
                    }
                },
            },
        },
    )
    return response["output"]["text"]
```

---

## Amazon Connect Specific Constraints

- **インスタンス**: 既存インスタンスへの統合（新規作成ではなく既存へのフロー追加）
- **コンタクトフロー言語**: JSON（Connect フローエディタでエクスポート/インポート）
- **Lex ボット**: Amazon Lex v2（V1 は非推奨のため使用禁止）
- **Chat SDK**: Amazon Connect Chat SDK v3（iOS: Swift / Android: Kotlin）
- **コンタクトフロー内 Lambda 呼び出し**: タイムアウト 8 秒以内（Connect の制約）
- **音声言語**: ja-JP

---

## CI/CD Pipeline (GitHub Actions)

```yaml
# 概要のみ（詳細は CDK スタックで生成）
steps:
  - lint: ruff check + mypy
  - test: pytest (unit, moto-mocked)
  - cdk-synth: CDK synth（差分確認）
  - cdk-deploy: CDK deploy（dev 環境は自動、staging/prod は手動承認）
```

---

## Environment Variables (Runtime, via Secrets Manager)

| 変数名 | 用途 |
|---|---|
| `KNOWLEDGE_BASE_ID` | Bedrock Knowledge Base ID |
| `HISTORY_TABLE_NAME` | DynamoDB 顧客履歴テーブル名 |
| `SUGGESTIONS_TABLE_NAME` | DynamoDB 改善提案テーブル名 |
| `CONNECT_INSTANCE_ID` | Amazon Connect インスタンス ID |
| `BEDROCK_MODEL_ID` | 使用する Bedrock モデル ARN |
| `CRAWLER_TARGET_URL` | クローリング起点 URL |
| `CONTENT_BUCKET_NAME` | S3 バケット名 |
