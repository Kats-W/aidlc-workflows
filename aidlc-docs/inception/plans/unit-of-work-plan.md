# Unit of Work 分解計画

# au Jibun Bank AI Agent

**作成日**: 2026-06-02
**ステータス**: Part 2 — Generation（承認済み）

---

## 前提情報（確定済み）

実行計画・アプリケーション設計から以下が確定している。

| Unit | 名称 | 主コンポーネント | 規模 |
|---|---|---|---|
| U-01 | Core Infrastructure | CDK SharedInfraStack、VPC/IAM/Secrets Manager | M |
| U-02 | Knowledge Pipeline | CrawlerLambda、EmbedderLambda、VectorStore DynamoDB | L |
| U-03 | AI Conversation Engine | RagHandlerLambda、BedrockClient、PiiMasker、HistoryRepository | L |
| U-04 | Omnichannel & Escalation | ChannelSwitchLambda、EscalationLambda、SessionContextManager | M |
| U-05 | SDK & Customer Profile | CustomerProfileLambda、CrmWriterLambda、IdentityHasher | M |
| U-06 | Self-Improvement Pipeline | ContactLensAnalyzerLambda、GapAnalyzerLambda、SuggestionGeneratorLambda | L |
| U-07 | Admin Dashboard | DashboardApiLambda、MetricsAggregatorLambda、React Amplify App | M |

依存順序:

```
U-01 → U-02 → U-03 → U-04（U-03 後に並列可）
                   → U-05（U-03 後に並列可）
                   → U-06 → U-07
```

---

## 分解計画チェックリスト

- [ ] `aidlc-docs/inception/application-design/unit-of-work.md` 生成
- [ ] `aidlc-docs/inception/application-design/unit-of-work-dependency.md` 生成
- [ ] `aidlc-docs/inception/application-design/unit-of-work-story-map.md` 生成
- [ ] コード組織化方針を unit-of-work.md に記載（Greenfield）
- [ ] ユニット境界・依存関係を検証
- [ ] 全ストーリーがいずれかのユニットに割り当てられていることを確認

## 確定した回答

| 質問 | 回答 | 内容 |
|---|---|---|
| Q1 コード組織化 | A案 | `src/` ユニット別パッケージ + `infra/` + `frontend/` + `tests/unit・integration/` |
| Q2 CDK スタック | A案 | ユニット対応 7 スタック（SharedInfraStack + 各ユニット） |
| Q3 U-04/U-05 順序 | A案 | U-04（Omnichannel & Escalation）を先に実装 |
| Q4 テスト構造 | A案 | `tests/unit/<pkg>/` + `tests/integration/` |
| Q5 Connect IaC | A案 | CDK で Connect フロー・Lex Bot も含め管理（GUI で初期作成後 JSON import） |

---

## 明確化が必要な質問

### Q1: コード組織化方針（ディレクトリ構造）

**カテゴリ**: Code Organization（Greenfield 必須）

現在の技術環境ドキュメントでは `src/` フラット構造が想定されていますが、
7 ユニット・14 Lambda の規模を踏まえ、以下のどちらを採用しますか？

**A案 — ユニット別パッケージ（推奨）**

```
src/
  crawler/        # U-02
  vector_store/   # U-02
  rag_handler/    # U-03
  session_manager/# U-03/U-04
  profile/        # U-05
  improvement_generator/ # U-06
  dashboard_api/  # U-07
  common/         # 共通（BedrockClient, PiiMasker）
infra/            # CDK スタック（全ユニット）
frontend/         # React (U-07)
tests/
  unit/
  integration/
```

**B案 — レイヤー別構造**

```
lambdas/          # 全 Lambda ハンドラ（handler.py のみ）
core/             # ビジネスロジック・リポジトリクラス
infra/            # CDK スタック
frontend/         # React
tests/
```

[Answer]: A案 — ユニット別パッケージ

---

### Q2: CDK スタック分割方針

**カテゴリ**: Technical Considerations

CDK スタックをどう分割しますか？

**A案 — ユニット対応スタック（推奨）**

- `SharedInfraStack`（U-01: VPC, IAM ロール, Secrets Manager, DynamoDB テーブル）
- `KnowledgePipelineStack`（U-02: CrawlerLambda, EmbedderLambda, EventBridge）
- `ConversationStack`（U-03: RagHandlerLambda, PersonalizerLambda, Connect 設定）
- `OmnichannelStack`（U-04: ChannelSwitchLambda, EscalationLambda）
- `ProfileStack`（U-05: CustomerProfileLambda, CrmWriterLambda）
- `ImprovementStack`（U-06: 3 Lambdas + EventBridge）
- `DashboardStack`（U-07: API Gateway, DashboardApiLambda, Amplify/Cognito）

**B案 — 2 スタックに統合**

- `InfraStack`（共有インフラ + DynamoDB 全テーブル）
- `AppStack`（全 Lambda + API GW + Amplify）

[Answer]: A案 — ユニット対応 7 スタック

---

### Q3: U-04 / U-05 の並列開発有無

**カテゴリ**: Team Alignment

実行計画では U-04（Omnichannel）と U-05（SDK & Customer Profile）は U-03 完了後に
**並列開発可能**と定義しています。開発者が 1 名の場合は順次実施になりますが、
どちらを先に実装しますか？

**A案** — U-04（Omnichannel & Escalation）を先に実装
**B案** — U-05（SDK & Customer Profile）を先に実装

[Answer]: A案 — U-04（Omnichannel & Escalation）を先に実装

---

### Q4: テスト戦略とカバレッジ境界

**カテゴリ**: Technical Considerations

ユニットテストのスコープをどう定義しますか？

**A案 — ユニット別テストディレクトリ**

```
tests/unit/crawler/
tests/unit/rag_handler/
tests/unit/vector_store/
...
tests/integration/   # ユニット間の統合テスト
```

**B案 — ファイル隣接テスト（pytest）**

```
src/crawler/handler.py
src/crawler/test_handler.py  # 同階層に配置
```

[Answer]: A案 — `tests/unit/<pkg>/` + `tests/integration/`

---

### Q5: Amazon Connect リソース（コンタクトフロー / Lex Bot）のスコープ

**カテゴリ**: Dependencies

Amazon Connect のコンタクトフロー定義・Lex v2 Bot 設定は CDK で管理しますか？
それとも AWS コンソールで手動設定しますか？

**A案** — CDK で IaC 管理（Connect フロー・Lex Bot も含む）
**B案** — Connect フロー・Lex Bot は手動設定、Lambda / DynamoDB 等の周辺リソースのみ CDK

注: CDK で Connect フローを管理する場合、`aws-connect` L1 Construct を使用します。
設定は可能ですが JSON 定義が大きくなります。

[Answer]: A案 — CDK で Connect フロー・Lex Bot も含め管理（GUI で初期作成後 CDK import）

---

## 承認フロー

1. 上記 5 問の `[Answer]:` を埋めて回答してください
2. 回答確認後、必要に応じてフォローアップ質問を追加します
3. 承認後、3 ファイルを生成します
