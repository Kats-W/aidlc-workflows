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
- [x] PR #38 マージ後、test-rag-handler.yml 再実行: pii masked entities:0（PII修正は有効）だが
      新規バグ TIMEOUT_BUDGET_EXCEEDED で hit:false（mask完了 03:41:16.601 → timeout 03:41:21.975、
      search complete / generated answer のログなし。Lambda Duration 9.3s, Init 1.65s）
- [x] PR #39: src/rag_handler/handler.py に各ステップ（mask/personalize/embed/search/
      generate_answer/history_append）の所要時間ログ追加 + RAG回答 max_tokens を
      1024→400 に短縮（ANSWER_MAX_TOKENS、Claude生成時間短縮 + 音声TTS向け簡潔化）
- [ ] 電話番号 +1 825-395-4670 接続不可問題（AWS サポートケース対応中、並行進行）
- [x] PR #39 マージ後、test-rag-handler.yml 再実行で pipeline step timing ログを確認:
      mask/personalize/embed 完了後、search ステップで予算超過（generate_answer 未到達）。
      原因特定: CosineSimilaritySearcher が cold start 時に VectorStore.scan_all()
      （DynamoDB 全件スキャン、PAY_PER_REQUEST・1024次元埋め込み・~1MB/ページ）を実行しており、
      6秒予算の残り時間（mask/personalize/embed後で約5.7秒）を超過していた
- [x] 恒久対応（S3プリビルドベクトルキャッシュ）実装:
      - src/vector_store/vector_cache_store.py 新規追加（VectorCacheS3Store: 結合済み
        embedding行列(.npy) + メタデータ(JSON) を crawlBucket の vector-cache/ 配下に保存・読込、
        build_matrix_and_meta ヘルパー）
      - EmbedderLambda (src/vector_store/handler.py): upsert/delete 適用後にVectorStore全件を
        再スキャンし vector-cache を再構築（S3書き込み失敗時はログのみ、ハンドラは失敗させない）
      - RagHandlerLambda の CosineSimilaritySearcher (src/vector_store/searcher.py):
        /tmp キャッシュ未生成時、まずS3 vector-cacheを読み込み（ヒット時はDynamoDB scanをスキップ）。
        ObjectNotFoundError（未構築）/ S3AccessError 時は従来の scan_all() にフォールバック
      - CDK: KnowledgePipelineStack に embedderRole 用 VectorCacheWrite (s3:PutObject)、
        ConversationStack に ragRole 用 VectorCacheRead (s3:GetObject)、
        CRAWL_CONTENT_BUCKET 環境変数を追加
      - テスト追加: test_vector_cache_store.py（4件）、test_searcher.py に S3キャッシュ
        ヒット/未構築/アクセスエラーのフォールバック3件、test_handler.py（embedder）新規4件
      - 既知の制約: 初回デプロイ直後は vector-cache が未構築のため scan_all() に
        フォールバック（従来どおり）。次回クロール/embedder実行後にキャッシュが
        構築され、以降のRagHandler呼び出しでDynamoDB scanを回避できる
- [x] PR #40/#41 マージ・デプロイ後、Run Embedder（run 27266880118）正常完了 →
      test-rag-handler.yml 再実行（run 27266934359）で vector-cache 有効化を確認:
      "vector cache loaded from s3"（rows:350）、search complete（hits:5、
      elapsed_ms:598、従来は予算超過でタイムアウトしていた箇所）。
      S3プリビルドベクトルキャッシュの恒久対応は本番で正常動作と確認
- [x] 上記再実行で新規バグ発見: search 後の generate_answer で
      BEDROCK_ERROR ValidationException → hit:false（依然として）。
      原因特定: ap-northeast-1 では Claude Sonnet 4.6 の on-demand invoke_model に
      bare な foundation-model ID（anthropic.claude-sonnet-4-6-20250514-v1:0）が
      使用不可。JP geographic inference profile
      （jp.anthropic.claude-sonnet-4-6-20250514-v1:0、東京/大阪にルーティング）の
      指定が必要（WebSearch で確認、AWS docs MCP 不使用時のため優先度2の情報源）
- [x] generate_answer モデルID修正:
      - src/common/bedrock_client.py: ANSWER_MODEL_ID を
        "anthropic.claude-sonnet-4-6-20250514-v1:0" →
        "jp.anthropic.claude-sonnet-4-6-20250514-v1:0" に変更
        （generate_answer / generate_suggestion / analyze_gap 全てに適用）
      - IAM: inference profile 呼び出しには foundation-model ARN と
        inference-profile ARN の両方への bedrock:InvokeModel 許可が必要
        - shared_infra_stack.ts: Lambda permission boundary の BedrockInvoke に
          inference-profile/* を追加
        - conversation_stack.ts: ragRole の BedrockEmbedAndAnswer に
          inference-profile/jp.anthropic.claude-sonnet-4-6-20250514-v1:0 を追加
        - improvement_stack.ts: suggestionRole / gapRole の
          BedrockSuggestion / BedrockGapAnalysis に同 inference-profile ARN を追加
      - 品質ゲート確認済み: ruff check（pass）/ mypy src tests（pass）/
        pytest tests/unit -q（330 passed、test_channel_switch.py の
        既知無関係failure 1件のみ）/ npx tsc --noEmit（pass）/
        npx cdk synth --context env=dev（全7スタック成功）
- [x] PR #42 マージ・デプロイ後、test-rag-handler.yml 再実行（run 27271893055）も
      依然 BEDROCK_ERROR ValidationException → hit:false。
      モデルID再調査（WebSearch 3件、scripts/aidlc-evaluator/config/sonnet-4-6.yaml の
      既存設定 `global.anthropic.claude-sonnet-4-6` とも整合）の結果、
      Claude Sonnet 4.6 以降は旧モデルの `-20250514-v1:0` のような日付/バージョン
      サフィックスが廃止されており、正しい ID は:
      - foundation-model: `anthropic.claude-sonnet-4-6`（サフィックスなし）
      - JP inference profile: `jp.anthropic.claude-sonnet-4-6`（東京/大阪）
      であると判明。前回の修正で付与した `-20250514-v1:0` サフィックスが誤りだった
- [x] モデルID再修正:
      - src/common/bedrock_client.py: ANSWER_MODEL_ID を
        "jp.anthropic.claude-sonnet-4-6-20250514-v1:0" →
        "jp.anthropic.claude-sonnet-4-6" に変更
      - conversation_stack.ts / improvement_stack.ts: foundation-model ARN /
        inference-profile ARN からも `-20250514-v1:0` サフィックスを削除
      - bedrock_client.py: ClientError から Code に加えて Message も例外メッセージに
        含めるよう変更（embed/generate_answer/generate_suggestion/analyze_gap 全て）。
        次回 ValidationException が再発した場合に rag pipeline failed ログの
        detail フィールドで具体的な原因を確認できるようにする診断改善
      - 品質ゲート確認済み: ruff check（pass）/ mypy src tests（pass）/
        pytest tests/unit -q（330 passed、test_channel_switch.py の
        既知無関係failure 1件のみ）/ npx tsc --noEmit（pass）/
        npx cdk synth --context env=dev（全7スタック成功）
- [ ] PRマージ後、cdk-deploy-dev 完了を確認 → test-rag-handler.yml 再実行で
      hit:true / answer / sources 確認、BEDROCK_ERROR/ValidationException が
      解消されたことを確認（再発時は詳細診断ログの detail フィールドを確認）
- [x] PR #43 マージ・デプロイ後、test-rag-handler.yml 再実行（run 27280928457）:
      モデルID修正（サフィックスなし）は有効で ValidationException は解消。
      新規エラー: BEDROCK_ERROR AccessDeniedException → hit:false（依然として）。
      診断ログ（detail フィールド）に具体的な原因が出力された:
      "...is not authorized to perform: bedrock:InvokeModel on resource:
       arn:aws:bedrock:ap-northeast-3::foundation-model/anthropic.claude-sonnet-4-6..."
      → jp.* geographic inference profile は東京(ap-northeast-1)だけでなく
      大阪(ap-northeast-3)にもルーティングされうるため、呼び出し元IAMロールには
      両リージョンの foundation-model ARN への bedrock:InvokeModel 許可が必要
      （WebSearch で確認: Geographic cross-Region inference は宛先リージョンを
      明示的に列挙する必要がある）
- [x] IAM修正: ap-northeast-3 の foundation-model ARN を追加
      - shared_infra_stack.ts: Lambda permission boundary の BedrockInvoke に
        `arn:aws:bedrock:ap-northeast-3::foundation-model/*` を追加
      - conversation_stack.ts: ragRole の BedrockEmbedAndAnswer に
        `arn:aws:bedrock:ap-northeast-3::foundation-model/anthropic.claude-sonnet-4-6` を追加
      - improvement_stack.ts: suggestionRole / gapRole の
        BedrockSuggestion / BedrockGapAnalysis に同ARNを追加
      - 品質ゲート確認済み: npx tsc --noEmit（pass）/
        npx cdk synth --context env=dev（全7スタック成功）
- [x] PR #44 マージ後、test-rag-handler.yml 再実行（run 27282495439、
      head_sha 82dac306、PR #44 のマージコミット）でも同一の
      AccessDeniedException（ap-northeast-3 foundation-model）が再発 → hit:false。
      原因調査の結果、PR #44 は `.github/workflows/auto-merge.yml`
      （`gh pr merge --squash --auto`、GITHUB_TOKEN 使用）により
      github-actions[bot] が自動マージ（merged_by: github-actions[bot]）。
      GitHub の仕様上、GITHUB_TOKEN に起因する push イベントは新たな
      ワークフロー実行をトリガーしないため、ci.yml（cdk-deploy-dev）が
      一度も実行されず、PR #44 のIAM修正コードは main にマージ済みだが
      AWS には未デプロイの状態だった
      （PR #43 は merged_by: Kats-W で人間が手動マージしたため正常に
      cdk-deploy-dev がトリガーされていた、との対比で確認）
- [x] ユーザーが ci.yml を main ブランチに対して workflow_dispatch で手動実行
      （run 27284242222、commit 82dac306、14:41:54Z〜14:54:08Z）→
      cdk-deploy-dev 完了。直後の test-rag-handler.yml 再実行（run 27285045699）で
      AccessDeniedException は解消（embed: 268ms, search: 669ms, hits:5、
      BEDROCK_ERRORなし）。PR #42-44 のIAM/モデルID修正は正しく機能していることを確認
- [x] 上記再実行で新規発見: generate_answer（Claude Sonnet 4.6, jp.* 推論
      プロファイル, max_tokens=400）が完了せず、6秒パイプラインバジェットで
      `TIMEOUT_BUDGET_EXCEEDED` → hit:false。
      Lambda総Duration 6841ms + Init(コールドスタート) 1571ms = 8413ms で
      Connect のハード上限 8秒も超過するリスクあり。
      → Sonnet 4.6 の応答生成速度は音声チャネルの8秒制約に対して根本的に厳しい
      （ユーザーと協議の結果、generate_answer のみ高速モデルに切替する方針に決定）
- [x] generate_answer を Claude Haiku 4.5 JP geo推論プロファイルに切替
      （WebSearchで確認: モデルID `anthropic.claude-haiku-4-5-20251001-v1:0`、
      JP推論プロファイル `jp.anthropic.claude-haiku-4-5-20251001-v1:0`、
      東京(ap-northeast-1)/大阪(ap-northeast-3)の国内リージョンに限定。
      Haiku 4.5 はSonnet 4.6と異なり `-20251001-v1:0` サフィックスを保持）
      - src/common/bedrock_client.py: 新規定数
        `RAG_ANSWER_MODEL_ID = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"`
        を追加し、generate_answer はこちらを使用。
        `ANSWER_MODEL_ID`（Sonnet 4.6）は generate_suggestion / analyze_gap
        （U-06、レイテンシ制約なし）専用として継続使用
      - conversation_stack.ts: ragRole の BedrockEmbedAndAnswer を
        Sonnet 4.6 ARN群 → Haiku 4.5 ARN群（東京/大阪 foundation-model +
        inference-profile）に置き換え（embed は引き続き Titan のみ）
      - improvement_stack.ts / shared_infra_stack.ts: 変更なし
        （boundary は foundation-model/* ワイルドカードで Haiku もカバー済み、
        suggestionRole/gapRole は引き続き Sonnet 4.6 を使用）
      - 品質ゲート確認済み: ruff check（pass）/ mypy src tests（pass）/
        pytest tests/unit -q（330 passed、test_channel_switch.py の
        既知無関係failure 1件のみ）/ npx tsc --noEmit（pass）/
        npx cdk synth --context env=dev（全7スタック成功）
- [x] PR #45 マージ後、ci.yml を main に対して workflow_dispatch で手動実行 →
      cdk-deploy-dev 完了。test-rag-handler.yml 再実行で hit:true / answer
      確認。TIMEOUT_BUDGET_EXCEEDED 解消。Claude Haiku 4.5
      （jp.anthropic.claude-haiku-4-5-20251001-v1:0）への切替によりRAG音声
      パイプラインが8秒バジェット内で正常動作することを確認
- [x] run-crawler.yml を手動実行（全ページ + FAQ クロール開始）。
      check-crawler.yml で結果確認: crawled:327 / added:79 / changed:138 /
      deleted:1 / errors:[]。ただし15分のLambdaタイムアウトで打ち切られ、
      remaining_queue:22674 と判明 → クローラートラップ（クエリ文字列付き
      内部リンクが無限に新規URL扱いされる現象）の疑い
- [x] クローラートラップ対策: src/crawler/handler.py の _normalize_url を
      ホスト別のクエリ文字列正規化に変更
      - help.jibunbank.co.jp（FAQ）: `id` パラメータのみ保持（ユーザー確認:
        FAQはクエリ文字列で出し分けており id パラメータが必須、それ以外は不要）
      - www.jibunbank.co.jp（コーポレートサイト）: クエリ文字列を完全除去
        （ユーザー確認: クエリ文字列なしでも大きな影響なし）
      - tests/unit/crawler/test_handler.py 新規追加（_normalize_url の
        フラグメント除去/ルートパス補完/ホスト別クエリ正規化を検証）
      - 品質ゲート確認済み: ruff check（pass）/ mypy src tests（pass）/
        pytest tests/unit -q（336 passed、test_channel_switch.py の
        既知無関係failure 1件のみ）
- [ ] PRマージ・cdk-deploy-dev 後、run-crawler.yml を再実行してクローラー
      トラップが解消され全ページ + FAQ のクロールが収束することを確認
- [ ] エンドツーエンド動作確認（connect-setup-guide.md チェックリスト、電話接続問題解消後）
