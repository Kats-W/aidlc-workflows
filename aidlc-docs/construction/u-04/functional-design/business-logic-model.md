# U-04 Omnichannel & Escalation — Business Logic Model

# （チャネル切り替え・文脈引き継ぎ・有人エスカレーション）

U-04 はセッション中の音声⇔チャット切り替え（同一 ContactId での文脈引き継ぎ）と、
ナレッジ未ヒット時の有人エスカレーションを担当する。対象ユーザーストーリーは
US-4.1（チャネル切り替え）, US-4.2（文脈引き継ぎ）, US-4.3（有人エスカレーション）。

---

## 1. コンポーネント構成

| コンポーネント | 実装 | 責務 |
| --- | --- | --- |
| ChannelSwitchLambda | `src/session_manager/channel_switch.py` `handler` | チャネル切り替え時に引き継ぎサマリーを生成して返す |
| SessionContextManager | 同ファイル | CustomerHistory の `SESSION#` エントリの読み書き・要約 |
| EscalationLambda | `src/rag_handler/escalation.py`（U-03 生成済み） | `hit=false` 等での有人転送属性を返す |
| OmnichannelStack | `infra/lib/stacks/omnichannel_stack.ts` | Lambda・IAM・エスカレーションキュー配線 |

---

## 2. チャネル切り替えフロー（US-4.1 / US-4.2）

```
Connect contact flow（音声 or チャット）
  └─ チャネル切り替えイベント発生（同一 ContactId）
       └─ ChannelSwitchLambda.handler(event)
            1. event から contactId / channelFrom / channelTo を取得
            2. SessionContextManager.get(contactId) で SESSION# 文脈を取得
                 - 見つかれば直近 N ターン（既定 5）から引き継ぎサマリー生成
                 - SessionNotFoundError なら空サマリーで新規セッション開始
            3. 戻り値: { handover_summary, channel_from, channel_to, turn_count }
       └─ contact flow が handover_summary を次チャネルのプロンプトに注入
```

- **文脈引き継ぎ**は `SESSION#<contactId>` の `turns`（最大 20）を `顧客:`/`AI:` 形式で連結。
- セッションへのターン追記は `SessionContextManager.update()`（最大 20 ターン保持、超過分は古いものから破棄）。

---

## 3. セッション要約フロー

```
SessionContextManager.summarize(contactId, last_n=5)
  └─ get(contactId) で SessionContext 取得
       └─ 直近 last_n ターンを古い順に整形
            "顧客: <text>"（role=user） / "AI: <text>"（role=assistant）
            改行（\n）区切りで連結して返す
```

---

## 4. 有人エスカレーションフロー（US-4.3）

```
RAG handler が hit=false を報告（U-03）
  └─ contact flow が EscalationLambda を呼ぶ（U-03 実装）
       └─ ESCALATION_QUEUE_ARN を返却
  └─ contact flow の TransferToQueue ブロックで有人キューへ転送
```

- U-04 では **CDK スタック**でエスカレーションキュー ARN を SSM から解決し、
  TransferToQueue 配線として出力（`EscalationQueueArn`）する。
- エスカレーション判定ロジック自体は U-03 の責務であり、U-04 は配線とキュー設定を担う。

---

## 5. 非同期 I/O

- すべての DynamoDB アクセスは `asyncio.to_thread` で boto3 同期呼び出しをラップ。
- Lambda エントリは `lambda_handler`（同期）→ `asyncio.run(handler(...))`。
