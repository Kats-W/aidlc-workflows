# 実行計画（Execution Plan）

# au Jibun Bank AI Agent

**作成日**: 2026-06-02
**プロジェクト種別**: グリーンフィールド / 高複雑度

---

## 1. 変更影響度アセスメント

| 観点 | 評価 | 内容 |
|---|---|---|
| ユーザー影響 | **高** | 個人顧客・オペレーター・管理者の全ユーザータイプへ直接影響 |
| 構造的変更 | **高** | 新規システム全体（Amazon Connect + Lex + Bedrock + DynamoDB + Amplify）の構築 |
| データモデル変更 | **高** | 5 テーブル新規設計（ベクトルストア・履歴・提案・差分管理・CSAT） |
| API 変更 | **高** | Connect フロー・Lambda ハンドラー・Amplify API・CRM 連携口の新規設計 |
| NFR 影響 | **高** | 8 秒以内の Lambda 応答、PII マスク、セキュリティ拡張適用、コスト管理 |
| **リスクレベル** | **HIGH** | 複数 AWS サービス統合・金融業界コンプライアンス・初回本番構築 |

---

## 2. ワークフロー可視化

```
[ユーザーリクエスト]
        |
        v
+-----------------------------------------------+
|           INCEPTION PHASE                     |
+-----------------------------------------------+
|  [完了] Workspace Detection                   |
|  [スキップ] Reverse Engineering (GF)          |
|  [完了] Requirements Analysis                 |
|  [完了] User Stories                          |
|  [実行中] Workflow Planning                   |
|  [EXECUTE] Application Design                 |
|  [EXECUTE] Units Generation                   |
+-----------------------------------------------+
        |
        v
+-----------------------------------------------+
|           CONSTRUCTION PHASE                  |
|   (Unit 1〜7 を順次処理)                      |
+-----------------------------------------------+
|  [EXECUTE] Functional Design (複雑ユニット)   |
|  [EXECUTE] NFR Requirements                   |
|  [EXECUTE] NFR Design                         |
|  [EXECUTE] Infrastructure Design              |
|  [ALWAYS]  Code Generation                    |
|  [ALWAYS]  Build and Test                     |
+-----------------------------------------------+
        |
        v
+-----------------------------------------------+
|           OPERATIONS PHASE                    |
+-----------------------------------------------+
|  [PLACEHOLDER] Operations                     |
+-----------------------------------------------+
```

---

## 3. フェーズ実行計画

### INCEPTION PHASE

| フェーズ | 判定 | 根拠 |
|---|---|---|
| Workspace Detection | ✅ 完了 | 常時実行 |
| Reverse Engineering | ⏭ スキップ | グリーンフィールド |
| Requirements Analysis | ✅ 完了 | 常時実行 |
| User Stories | ✅ 完了 | 複数ユーザータイプ・複雑業務要件 |
| Workflow Planning | ✅ 実行中 | 常時実行 |
| **Application Design** | **▶ EXECUTE** | 新規コンポーネント多数（Lambda×8、DynamoDB×5、Connect フロー）、サービスレイヤー設計が必要 |
| **Units Generation** | **▶ EXECUTE** | 7 ユニットへの分解・依存関係整理が必要 |

### CONSTRUCTION PHASE（各ユニット共通）

| フェーズ | 判定 | 根拠 |
|---|---|---|
| **Functional Design** | **▶ EXECUTE** | 複雑なビジネスロジック（RAG 検索・コサイン類似度・PII マスク・自己改善分析）あり |
| **NFR Requirements** | **▶ EXECUTE** | 8 秒 Lambda 制約・セキュリティ拡張適用・コスト管理要件あり |
| **NFR Design** | **▶ EXECUTE** | リトライ・キャッシュ・PII マスクパターンの設計が必要 |
| **Infrastructure Design** | **▶ EXECUTE** | 全ユニットで新規 AWS リソース（CDK スタック）が必要 |
| Code Generation | ✅ ALWAYS | 常時実行 |
| Build and Test | ✅ ALWAYS | 常時実行 |

---

## 4. ユニット分解計画（Units of Work）

| Unit | 名称 | 主ストーリー | 依存 Unit | 規模 |
|---|---|---|---|---|
| **U-01** | Core Infrastructure | — | なし | M |
| **U-02** | Knowledge Pipeline | US-2.1, 2.2, 2.3 | U-01 | L |
| **U-03** | AI Conversation Engine | US-1.1, 1.2, 1.3, 1.4, 6.1, 6.2 | U-01, U-02 | L |
| **U-04** | Omnichannel & Escalation | US-4.1, 4.2, 4.3 | U-01, U-03 | M |
| **U-05** | SDK & Customer Profile | US-5.1, 5.2, 6.3 | U-01, U-03 | M |
| **U-06** | Self-Improvement Pipeline | US-3.1, 3.2, 3.3 | U-01, U-03 | L |
| **U-07** | Admin Dashboard | US-7.1, 7.2, 7.3 | U-01, U-06 | M |

### 推奨実装シーケンス（依存関係順）

```
U-01 (Infrastructure)
  └─> U-02 (Knowledge Pipeline)
        └─> U-03 (AI Conversation Engine)
              ├─> U-04 (Omnichannel)
              ├─> U-05 (SDK & Customer Profile)
              └─> U-06 (Self-Improvement)
                    └─> U-07 (Admin Dashboard)
```

U-04 / U-05 は U-03 完了後に並列開発可能。

---

## 5. リスクと緩和策

| リスク | 影響度 | 緩和策 |
|---|---|---|
| Connect + Lex 日本語統合の動作確認 | 高 | U-01 完成直後に音声・チャット疎通テストをローカルで実施 |
| Lambda 8 秒タイムアウト（RAG 処理） | 高 | DynamoDB スキャン結果をコールドスタートキャッシュ（/tmp）に保持 |
| OpenSearch Serverless の誤プロビジョニング | 高 | CDK で Bedrock KB は使わない設計（カスタム RAG のみ）を明示 |
| PII がログに混入するリスク | 高 | Comprehend マスク後のみ保存・Security Extension ブロッキング適用 |
| DynamoDB ベクトル全件スキャンのコスト増 | 中 | Lambda /tmp キャッシュ（TTL 15 分）でスキャン頻度を抑制 |

---

## 6. 成功基準

| 基準 | 内容 |
|---|---|
| **一次解決率** | AI エスカレーションなし終了 ≥ 70% |
| **応答時間** | Lambda P99 ≤ 5 秒（Connect 制約 8 秒以内） |
| **月額コスト** | 100 セッション未満で ≤ 5,000 円 |
| **ナレッジ更新** | 週次クローリング → 翌日中にベクトルストア反映 |
| **テストカバレッジ** | 内部コードパス 80% 以上 |
| **セキュリティ** | Security Extension 全ルール準拠（ブロッキング 0 件） |
