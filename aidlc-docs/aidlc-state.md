# AI-DLC State Tracking

## Project Information

- **Project Name**: au Jibun Bank AI Agent
- **Project Type**: Greenfield
- **Start Date**: 2026-06-01T22:40:00Z
- **Current Stage**: OPERATIONS PHASE — エンドツーエンド動作確認フェーズ
- **Depth Level**: Comprehensive

## Workspace State

- **Existing Code**: No (greenfield)
- **Reverse Engineering Needed**: No
- **Workspace Root**: /home/user/aidlc-workflows

## Code Location Rules

- **Application Code**: Workspace root (NEVER in aidlc-docs/)
- **Documentation**: aidlc-docs/ only
- **Structure patterns**: See code-generation.md Critical Rules

## Extension Configuration

| Extension | Enabled | Decided At |
|---|---|---|
| Security Baseline | Yes | Requirements Analysis |
| Property-Based Testing | Yes (Full) | Requirements Analysis |

## Stage Progress

### INCEPTION PHASE

- [x] Workspace Detection
- [ ] Reverse Engineering (skipped — greenfield)
- [x] Requirements Analysis
- [x] User Stories
- [x] Workflow Planning
- [x] Application Design (COMPLETE)
- [x] Units Generation (COMPLETE)

### CONSTRUCTION PHASE

- [x] Per-Unit Loop (COMPLETE)
  - [x] U-01 Core Infrastructure — COMPLETE
  - [x] U-02 Knowledge Pipeline — COMPLETE
  - [x] U-03 Conversation Engine — COMPLETE
  - [x] U-04 Omnichannel & Escalation — COMPLETE
  - [x] U-05 SDK & Customer Profile — COMPLETE
  - [x] U-06 Self-Improvement Pipeline — COMPLETE
  - [x] U-07 Admin Dashboard — COMPLETE
- [x] Build and Test (306 tests pass, ruff/mypy clean — verified by independent sub-agent)

### OPERATIONS PHASE

- [x] CI/CD Pipeline (GitHub OIDC + CDK auto-deploy — COMPLETE)
- [x] PR #3 マージ → main（squash merge、d16663b）
- [x] dev 環境デプロイ確認（全7スタック完了: PR #10〜#13、2026-06-06）
- [x] Amazon Connect コンタクトフロー設定（CDK実装: CfnContactFlow + HoursOfOp + EscalationQueue + Lambda permissions）
- [x] CI正常完了、電話番号（+1 825-395-4670）→ au-jibun-bank-dev-ai-agent フロー割り当て完了
- [x] PR #31: check-crawler.yml 修正（$STREAM 二重行バグ、grep パターン拡張、--limit 10000）
- [x] PR #32: IAM Permission Boundary に dynamodb:Scan 追加（DifferEngine._load_stored_hashes 対応）
- [x] PR #33: IAM Permission Boundary に lambda:InvokeFunction / comprehend:DetectPiiEntities / connect:* 追加（横展調査による先行修正）
- [x] PR #34: Embedder バッチ化（50件/バッチ、1MB上限対応）+ 例外キャッチ（Lambda auto-retry 起因の ContentDiff 破壊防止）
- [x] PR #35: check-embedder.yml 追加（EmbedderLambda ログ確認ワークフロー）
- [x] クローラー本番稼働確認（crawled:327 / added:327 / changed:22 / deleted:0 / errors:[]）
- [x] Embedder 本番稼働確認（upserted:50 / deleted:0、Bedrock Titan v2 & VectorStore 書き込み正常）
- [x] Connect コンソール: Lambda 関数関連付け確認（au-jibun-bank-dev-{rag-handler,escalation,personalizer,csat-handler,channel-switch}、ユーザーにより手動確認完了）
- [x] PR #37: test-rag-handler.yml 追加（RagHandlerLambda 直接呼び出しによるRAGパイプライン検証ワークフロー、電話E2E代替）
- [x] test-rag-handler.yml 初回実行: hit:false（COMPREHEND_ERROR ValidationException を検出）
- [x] 重大バグ特定: PiiMasker.mask() の既定 lang="ja" で Comprehend DetectPiiEntities を呼ぶと
      ValidationException（ja は en/es のみ対応の API）→ 全日本語入力が PII マスク段階で
      フォールバックし、RAG パイプライン本体（embed/検索/Claude生成）が未実行だった
- [x] PR #38: src/common/ja_pii_patterns.py 追加（電話番号・メール・郵便番号・カード番号・
      マイナンバーを正規表現でマスク）。lang="ja" は Comprehend を呼ばずこちらを使用（320 tests pass）
- [ ] 電話番号 +1 825-395-4670 接続不可問題（AWS サポートケース対応中、並行進行）
- [ ] PR #38 マージ後、test-rag-handler.yml 再実行で hit:true / answer / sources 確認
- [ ] エンドツーエンド動作確認（connect-setup-guide.md チェックリスト、電話接続問題解消後）
