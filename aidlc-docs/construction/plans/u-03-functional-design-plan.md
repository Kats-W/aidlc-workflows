# U-03 AI Conversation Engine — Functional Design Plan

- [x] ビジネスロジックモデル（RAG 8秒パイプライン・パーソナライズ・フォールバック）を定義する
- [x] ビジネスルール（8秒制約・PII 必須マスク・エスカレーション条件）を定義する
- [x] ドメインエンティティ（ConversationTurn・RagAnswer・SessionContext）を定義する
- [x] 対象ユーザーストーリー: US-1.1, US-1.2, US-1.3, US-1.4, US-6.1, US-6.2

## 成果物

| ファイル | 内容 |
| --- | --- |
| `u-03/functional-design/business-logic-model.md` | RAG パイプライン構成・各ステップの責務 |
| `u-03/functional-design/business-rules.md` | 強制ルール（時間予算・PII・エスカレーション・CSAT） |
| `u-03/functional-design/domain-entities.md` | 主要エンティティのスキーマと永続化形式 |
