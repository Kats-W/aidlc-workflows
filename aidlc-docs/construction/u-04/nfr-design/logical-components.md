# U-04 Omnichannel & Escalation — Logical Components

U-04 の論理コンポーネントと責務・依存関係。

---

## 1. コンポーネント一覧

| コンポーネント | 種別 | 実装 | 責務 |
| --- | --- | --- | --- |
| ChannelSwitchLambda | Lambda handler | `channel_switch.handler` | チャネル切り替えイベントを処理し引き継ぎサマリーを返す |
| SessionContextManager | ドメインサービス | `channel_switch.SessionContextManager` | `SESSION#` の取得・追記・要約 |
| SessionContext | エンティティ | `channel_switch.SessionContext` | セッション文脈の値オブジェクト |
| _format_turns | 純粋関数 | `channel_switch._format_turns` | ターン列を `顧客:`/`AI:` 形式に整形 |
| ConversationTurn | エンティティ（再利用） | `history.ConversationTurn` | 会話 1 ターン |
| EscalationLambda | Lambda handler（再利用） | `rag_handler.escalation.handler` | 有人転送属性の返却（U-03） |

---

## 2. 依存関係

```
ChannelSwitchLambda.handler
  └─ SessionContextManager
       ├─ DynamoDB CustomerHistory (SESSION#)   [GetItem / PutItem]
       ├─ ConversationTurn (src.session_manager.history)
       └─ _format_turns
  └─ errors: ValidationError / SessionNotFoundError / DynamoAccessError
```

- 外部依存は CustomerHistory テーブルのみ。Bedrock/Comprehend には依存しない。
- EscalationLambda は U-04 ランタイムから直接呼ばず、Connect contact flow が呼び出す。U-04 は CDK でキュー ARN を配線する。

---

## 3. SessionContextManager のメソッド責務

| メソッド | 入力 | 出力 | 例外 |
| --- | --- | --- | --- |
| `get` | `contact_id` | `SessionContext` | `ValidationError`, `SessionNotFoundError`, `DynamoAccessError` |
| `update` | `contact_id`, `ConversationTurn` | `None`（最大 20 ターン保持） | `ValidationError`, `DynamoAccessError` |
| `summarize` | `contact_id`, `last_n=5` | `str`（整形済みサマリー） | `SessionNotFoundError` |

---

## 4. インフラ論理コンポーネント（OmnichannelStack）

| 論理要素 | 役割 |
| --- | --- |
| ChannelSwitchLambda（Function） | Python 3.12 / 256MB / 10s |
| ChannelSwitchRole（IAM Role） | Permission Boundary + 最小権限ポリシー |
| CustomerHistorySessionReadWrite（Policy Stmt） | `GetItem/PutItem/Query`（テーブル ARN スコープ） |
| SharedCmk（KMS, import） | 暗号化/復号権限を Lambda ロールへ付与 |
| EscalationQueueArn（CfnOutput） | Connect TransferToQueue 配線用 |
| ChannelSwitchErrorAlarm（CloudWatch Alarm） | エラー率監視 |

---

## 5. 設定（環境変数 / SSM）

| 名前 | 取得元 | 用途 |
| --- | --- | --- |
| `CUSTOMER_HISTORY_TABLE_NAME` | SSM → Lambda env | SESSION# テーブル名 |
| `ESCALATION_QUEUE_ARN` | SSM → Lambda env | エスカレーション参照 |
| KMS CMK ARN | SSM | 暗号化/復号 grant |
| Permission Boundary ARN | SSM | IAM ロール境界 |
