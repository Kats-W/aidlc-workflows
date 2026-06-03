# U-04 Omnichannel & Escalation — Functional Design Plan

- [x] ビジネスロジックモデル（チャネル切り替え・文脈引き継ぎ・有人エスカレーション）を定義する
- [x] ビジネスルール（同一 ContactId 維持・最大20ターン保持・PII マスク済み前提・エスカレーション条件）を定義する
- [x] ドメインエンティティ（SessionContext・SESSION# スキーマ・ConversationTurn 再利用）を定義する
- [x] 対象ユーザーストーリー: US-4.1, US-4.2, US-4.3

## 成果物

| ファイル | 内容 |
| --- | --- |
| `u-04/functional-design/business-logic-model.md` | チャネル切り替えフロー・文脈引き継ぎ・エスカレーション連携 |
| `u-04/functional-design/business-rules.md` | 強制ルール（ContactId 維持・20ターン上限・PII・エスカレーション） |
| `u-04/functional-design/domain-entities.md` | SessionContext / SESSION# 永続化スキーマ |
