# au Jibun Bank AI Agent — Claude Code ガイドライン

## AWS リソース設計・実装の調査プロトコル

**推測で AWS リソースの設計・実装を進めることを禁止する。**
必ず以下の順序で情報を収集し、根拠のある実装のみ行う。

### 情報源の優先順位

| 優先度 | 情報源 | ツール | 備考 |
|--------|--------|--------|------|
| 1 | AWS 公式ドキュメント | `aws-docs` MCP (`search_documentation`, `read_documentation`) | 最優先。スキーマ・API リファレンスはここで確定させる |
| 2 | AWS 公式ブログ | `WebSearch` (allowed: `aws.amazon.com`) | 実装パターン・ベストプラクティス |
| 3 | AWS re:Post / GitHub awslabs | `WebSearch` / `WebFetch` | 既知の不具合・制約の確認 |
| 4 | 技術ブログ・Qiita | `WebSearch` | 公式情報で解決できない場合のみ。信頼性低に注意 |

### 必須調査パターン

#### Amazon Connect コンタクトフロー JSON
- `InvalidContactFlowException` が発生する前に必ずスキーマを調査する
- 各アクションタイプ（`GetParticipantInput`, `InvokeLambdaFunction` 等）の有効パラメータを確認
- 調査コマンド例:
  ```
  search_documentation("Amazon Connect contact flow GetParticipantInput parameters")
  search_documentation("Amazon Connect contact flow JSON schema InvokeLambdaFunction")
  ```

#### AWS CDK コンストラクト
- L2 コンストラクトが存在するか確認してから L1 (Cfn) を使う判断をする
- 調査コマンド例:
  ```
  search_documentation("AWS CDK CfnContactFlow content JSON schema")
  ```

#### Lambda / Bedrock / Comprehend API

- API パラメータ名・レスポンス形式は必ずリファレンスで確認
- 特に Connect ↔ Lambda 間の属性受け渡し（`$.External.*` パス）は要確認

---

## プロジェクト概要

au じぶん銀行 AI エージェント — Amazon Connect ベースの日本語音声 RAG システム。

### アーキテクチャ

```text
電話 / Chat → Amazon Connect → Lex v2 (ja-JP ASR)
                              → RagHandlerLambda → Bedrock (Claude Sonnet 4.6)
                              → 人間エスカレーション (エスカレーションキュー)
```

### スタック構成

<!-- markdownlint-disable MD060 -->
| スタック | 内容 |
|----------|------|
| `SharedInfra` (U-01) | KMS, DynamoDB x5, S3, Connect, Lex v2, Lambda Layer |
| `KnowledgePipeline` (U-02) | Crawler + Embedder Lambda, EventBridge |
| `Conversation` (U-03) | RagHandler, Personalizer, Escalation, CSAT Lambda |
| `Omnichannel` (U-04) | ChannelSwitch Lambda, AI Contact Flow |
| `Profile` (U-05) | CustomerProfile, CrmWriter Lambda |
| `Improvement` (U-06) | ContactLensAnalyzer, GapAnalyzer, SuggestionGenerator Lambda |
| `Dashboard` (U-07) | MetricsAggregator, DashboardApi Lambda, Cognito, API GW |
<!-- markdownlint-enable MD060 -->

### Lambda Layer

全 Lambda は `SharedInfra` で定義した `PythonDepsLayer` を使用する。
依存パッケージ: `boto3`, `aioboto3`, `aws-lambda-powertools`, `httpx`, `beautifulsoup4`, `numpy`, `msgpack`

CI `cdk-deploy-dev` ジョブで `pip install --platform manylinux2014_x86_64` によりビルドされる。

### Connect コンタクトフロー設計原則

- **スキーマ確定なしで実装禁止** — アクションタイプのパラメータは必ず公式ドキュメントで確認
- `Conditions: []` は Errors を持つすべての Transitions に必須
- Lambda レスポンス属性は `$.External.{key}` でアクセス（キー名は Python コードと一致させる）
- `GetParticipantInput` + Lex v2 の有効パラメータ: `Text`, `LexV2Bot.AliasArn`, `Timeout`
  - `LexSessionAttributes` は無効フィールド（確認済み: `InvalidContactFlowException` の原因）
- `UpdateContactTextToSpeechVoice` の有効パラメータ: `TextToSpeechVoice`, `TextToSpeechEngine`, `TextToSpeechStyle`
  - `VoiceId` は無効フィールド（確認済み: `InvalidContactFlowException` の原因）
  - Kazuha（ニューラル音声）使用時は `TextToSpeechEngine: 'neural'` を必ず指定する
- `MessageParticipant.Text` に `$.External.{key}` でLambdaレスポンスを直接参照可能（確認済み）
- `UpdateContactTargetQueue` の有効パラメータ: `QueueId`（ARN または ID）
- `Compare` の有効パラメータ: `ComparisonValue`（JSONPath式）

### 開発フロー

1. AWS リソースの仕様変更は必ず公式ドキュメントを参照
2. CDK TypeScript: `npx tsc --noEmit` で型チェック後にコミット
3. `cdk synth` で CloudFormation テンプレート検証
4. PR 作成 → CI (Python CI + CDK CI) 通過確認
5. main マージ後 `cdk-deploy-dev` でデプロイ確認

### 重要な既知制約

- Amazon Connect インスタンス: ap-northeast-1 のみ対応、カナダ電話番号 (+1 825/833) からの着信不可
- Lex v2 ja-JP: ap-northeast-1 で利用可能（`Kazuha` ニューラル音声）
- Lambda: Python 3.12、依存パッケージは Layer で提供（`Code.fromAsset` はソースのみ）
- Connect コンタクトフロー更新: 既存フローへの UPDATE は新規 CREATE と同じ JSON バリデーションが走る
