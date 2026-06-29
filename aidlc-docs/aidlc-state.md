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
- [x] PRマージ・cdk-deploy-dev 後、run-crawler.yml を再実行してクローラー
      トラップ修正の効果を確認: crawled:203 / added:0 / changed:12 /
      deleted:4 / remaining_queue:13164（22674から減少）。ただし BFS の
      queue/visited が呼び出し間で永続化されておらず毎回シードURLから
      再開するため、「収束」の根拠としては不十分と判断（同じ浅い
      フロンティアを再クロールしているだけの可能性が高い）
- [x] BFSキュー/visited状態の永続化（a案）を実装
      - src/crawler/state_store.py 新規追加: CrawlStateStore が
        S3 (`_state/bfs_state.json`) に queue/visited を保存・復元。
        既存の CrawlBucketWrite IAM ポリシー（`${crawlBucketArn}/*`）で
        カバーされるため CDK 変更は不要
      - src/crawler/handler.py: 起動時に永続化済み state をロードし
        BFS を再開（_initial_state ヘルパー）。queue が空（=1サイクル
        完了）の場合のみシードURL + 空visitedで新サイクルを開始
        （再クロールにより差分検出を継続）。リンク追加時に `queued`
        セットで重複エンキューを防止。ループ終了時（タイムアウト/
        キュー枯渇いずれも）に state_store.save で永続化
      - src/crawler/differ.py: DifferEngine.diff に
        `crawled_url_hashes` 引数を追加。stored にあって今回の
        crawlで見つからなかった chunk は、その chunk の url_hash が
        `crawled_url_hashes` に含まれる場合のみ「deleted」とする
        （= 今回のBFSで再訪していないページのchunkを誤って削除しない）。
        handler は ParseError（フェッチ成功・本文抽出失敗）の場合も
        url_hash を crawled_url_hashes に含める（FetchTimeoutError等の
        一時的失敗は含めない）
      - summary に remaining_queue を追加（モニタリング用）
      - tests/unit/crawler/test_state_store.py 新規追加（save/load
        ラウンドトリップ、未保存時None、不正JSON時None）
      - tests/unit/crawler/test_differ.py に crawled_url_hashes の
        スコープ動作のテストを追加
      - tests/unit/crawler/test_handler.py に _initial_state
        （新規開始/再開/サイクル完了時リセット）のテストを追加
      - 品質ゲート確認済み: ruff check（pass）/ mypy（pass）/
        pytest tests/unit -q（344 passed、test_channel_switch.py の
        既知無関係failure 1件のみ）/ npx tsc --noEmit（pass）
- [x] BFS永続化（a案）デプロイ後の初回 run-crawler.yml/check-crawler.yml で
      リグレッション発覚: CloudWatch Logsに
      `[ERROR] S3AccessError: failed to load BFS state` が3回（Lambda
      非同期呼び出しのデフォルト再試行込み）出力され、クロールが
      一切実行されなくなっていた。state_store.load() が
      get_object で NoSuchKey/404 以外の ClientError（権限エラー等）を
      受けると S3AccessError を re-raise し、handler() 内でこれを
      キャッチしていなかったため、_initial_state 到達前に handler 全体が
      未処理例外で異常終了していた
      - 修正: src/crawler/handler.py に _load_state ヘルパーを追加し、
        state_store.load() が S3AccessError を送出した場合は
        ログを残して loaded=None にフォールバック（永続化なしの
        従来動作＝シードURLから再開）。state_store.save() の呼び出しも
        try/except で囲み、保存失敗時もクロール結果（diff/embedder
        invoke）の処理は継続（次回起動時にシードから再開するのみ）
      - src/crawler/state_store.py: load/save失敗時に
        logger.exception で bucket/key/error_code を含む構造化ログを
        出力するよう変更（次回失敗時にCloudWatch上で根本原因
        （AccessDenied等のAWSエラーコード）を特定できるようにするため）
      - tests/unit/crawler/test_handler.py に
        test_load_state_falls_back_to_fresh_cycle_on_s3_access_error
        を追加（AccessDeniedを返すフェイクS3クライアントでフォール
        バック動作を検証）
      - 品質ゲート確認済み: ruff check（pass）/ mypy src tests（pass）/
        pytest -q（345 passed、test_channel_switch.py の既知無関係
        failure 1件のみ）
      - 根本原因（なぜ本番で _state/bfs_state.json への get_object が
        AccessDenied等になるか）は未特定。次回 run-crawler.yml 実行時の
        ログ（logger.exceptionで出力されるerror_code）で要確認
- [ ] PRマージ・cdk-deploy-dev 後、run-crawler.yml を複数回実行し、
      (1) S3AccessErrorが解消したか（または error_code から根本原因を
      特定できるか）、(2) remaining_queue が単調減少して最終的に
      1サイクル完了（queue空→新サイクルでvisitedリセット）することを確認
- [x] PR #50: BFS state ロード/セーブ失敗時のフォールバック修正
      （_load_state ヘルパー追加、state_store に構造化エラーログ）
- [x] PR #51: クローラーに s3:ListBucket 権限追加（crawl-content バケット）
- [x] PR #52: check-embedder.yml 改善（最新5ストリーム + timeout/REPORT行表示）
- [x] PR #53: Embedder の vector-cache リビルドを最終バッチのみに制限
      （rebuildCache フラグ導入、CrawlerLambda が最終バッチにのみ設定）
- [x] PR #54: EmbedderLambda タイムアウトを10分に延長（キャッシュリビルド対応）
- [x] PR #55: EmbedderLambda の Decimal 読み込みパスを排除
      （_FloatDeserializer で DynamoDB Number → float 直接変換、
      Decimal 中間オブジェクトによる GC スラッシング回避）
- [x] PR #56: sample-crawl-content.yml 診断ワークフロー追加
      （S3 から crawl 済みチャンクのサンプルを取得して内容確認）
- [x] PR #57-58: sample-crawl-content.yml の S3 エラーハンドリング改善
- [x] PR #59: クローラーで non-HTML レスポンス（PDF等）をスキップ
      （Content-Type チェック追加、BeautifulSoup が PDF バイナリを
      テキスト化して大量の無意味チャンクを生成する問題を回避）
- [x] PR #60-61: sample-crawl-content ワークフローの改善（ハッシュ指定対応等）
- [x] PR #62: SharedInfra VPC から未使用 NAT Gateway を削除（コスト削減）
- [x] PR #63: S3 vector cache の行数不一致ハンドリング（並行リビルド対策）
- [x] PR #64: vector cache を単一 atomic S3 オブジェクトとして保存
- [x] PR #65: RagHandler の S3 vector cache 未構築時タイムアウト回避
      （ObjectNotFoundError 時に空 corpus を返す設計に変更）
- [x] PR #66: RagHandlerLambda に s3:ListBucket 権限追加（vector-cache prefix）
- [x] EmbedderLambda OOM 問題の調査・対策（PR #67-71）:
      - 問題: 129,811 アイテム × 1024 次元の全件スキャン + キャッシュリビルドで
        Lambda が OOM（cgroup kill、サイレント失敗）
      - PR #67: EmbedderLambda メモリ増量 + タイムアウト延長
      - PR #68: キャッシュ書き込みのメモリ最適化（write path）
      - PR #69: vector cache メタデータから text を除外
        （text は DynamoDB BatchGetItem でクエリ時に取得する方式に変更）
        - VectorStore.batch_get_texts() 追加
        - CosineSimilaritySearcher.search() で top-k hit の text を
          batch_get_texts で取得するよう変更
      - PR #70: scan_all の ProjectionExpression から text を除外
        （~323 MB のメモリ節約）
      - PR #71: msgpack.pack → numpy.save + json.dump に置換
        （msgpack の内部バッファ倍増で 1,525 MB の追加メモリ確認済み、
        numpy.save/json.dump は追加メモリ 0 MB）
        - CloudWatch REPORT で確認: Duration 838s, Max Memory 2,022 MB / 3,072 MB
        - S3 キャッシュ形式: matrix.npy + meta.json（2ファイル構成）
- [x] PR #72: インクリメンタルキャッシュ更新に移行
      - 問題: full scan が 836秒（全体の99.3%）を占め、corpus 2-3倍で
        Lambda 15分上限を超過するスケーラビリティリスク
      - CloudWatch タイムスタンプ分析で内訳を確認:
        DynamoDB scan 836秒(99.3%) / matrix構築+S3 write 6秒(0.7%)
      - 対策: full scan + 全体リビルド → S3 インクリメンタルパッチに変更
        - VectorCacheS3Store.patch() 追加: 既存キャッシュを GET →
          upsert/delete の差分を適用 → PUT
        - 処理量が O(batch size) で corpus サイズに依存しない
        - rebuildCache フラグを廃止、各バッチが自分の差分を直接反映
        - CrawlerLambda から rebuildCache 関連コード削除
        - run-embedder.yml: cli-read-timeout を 920s → 120s に短縮
        - テスト更新: patch の create/append/replace/delete/combined テスト追加
- [x] PR #73: RagHandler IAM に dynamodb:BatchGetItem 追加
      - 問題: PR #72 のインクリメンタルキャッシュ移行で batch_get_texts
        （top-k の text を DynamoDB BatchGetItem で取得）を導入したが、
        ragRole の VectorStoreRead ポリシーに BatchGetItem が欠けていた
      - CloudWatch で DYNAMO_ACCESS_ERROR を確認、横展開調査で
        ragRole のみが BatchGetItem を必要とすることを確認
      - conversation_stack.ts: VectorStoreRead に dynamodb:BatchGetItem 追加
- [x] PR #74: DynamoDB コールド接続ウォームアップ + シングルトン再利用
      - 問題: PR #73 修正後、search が 669ms → 2,370ms に悪化。
        batch_get_texts が初回呼び出しで DynamoDB TCP 接続確立（~1,700ms）
        を行い、6s パイプラインバジェットを超過（TIMEOUT_BUDGET_EXCEEDED）
      - 対策1: VectorStore.warm_connection() 追加 — ダミー get_item で
        TCP 接続を事前確立。既存の dynamodb:GetItem IAM 権限で動作
      - 対策2: _build_dependencies() が _searcher_singleton を再利用
        するよう変更（毎回新しい VectorStore を生成していたため、
        シングルトンの warm 済み接続が使われていなかった）
      - _ensure_cache_warmed() で warm_connection() を呼び出し
        （6s バジェット外で実行）
- [x] PR #75: メモリキャッシュ + float32 統一
      - 問題: PR #74 修正後もコールドスタートで search 2,204ms。
        warm_connection は DynamoDB 接続のみ対象で、/tmp からの
        500MB .npy ファイル読み込み（~1,500ms）が主因と判明
      - 対策1: インメモリキャッシュ — 行列+メタデータをインスタンス変数に
        保持（TTL 付き）。ensure_cache_loaded() の S3 DL 時にメモリにも
        セットし、パイプラインの search で /tmp ディスク I/O をスキップ
      - 対策2: float64 → float32 統一 — 埋め込みは全て float32 で保存済み
        だが、searcher.py がクエリベクトルを float64 にキャストしていたため
        numpy が行列全体を float64 に暗黙アップキャスト（500MB → 1GB）。
        精度向上なし（元データが float32 の時点で有効精度7桁）。
        業界標準（FAISS/Pinecone等）も float32 以下
      - 期待効果: search ~2,200ms → ~400ms（コールド）/ ~300ms（ウォーム）
      - 留意: コーパス 2-3倍時にメモリ ~1-1.5GB 追加。Lambda 4,096MB で
        対応可だが、3倍超でメモリ増設（最大10,240MB）が必要
- [x] PR #75 デプロイ後の検証: test-rag-handler で search ≤ 500ms、
      hit:true、6s バジェット内を確認
      - 検証日 2026-06-23、2回連続実行とも hit:true
        - RUN 1（コールド）: search 858ms / generate 3,125ms / 合計 ~4,259ms
        - RUN 2（ウォーム）: search 553ms / generate 2,763ms / 合計 ~3,552ms
      - ウォーム search 553ms で #75 最適化の効果を確認（#75 前 ~1,800ms）
      - 6/22 の初回失敗は #75 のリグレッションではなく、Bedrock
        generate_answer のテールレイテンシ変動 × コールドスタートの
        重なりと判定（search ではなく生成中にタイムアウトしていた）
      - 残リスク: generate_answer が最大ボトルネック（2.7〜3.1s・変動大）。
        コールド路は生成が ~5s に振れると 6s 超過の脆さ。今後
        ANSWER_MAX_TOKENS 短縮 / ストリーミング化を独立検討
- [ ] エンドツーエンド動作確認（connect-setup-guide.md チェックリスト、電話接続問題解消後）
- [x] 電話番号の整理・全リリース（2026-06-27、コスト最小化）:
      - 保有3番号を全リリース（CA +1 825 DID / CA +1 833 トールフリー / AU +61 1800 トールフリー）
        - CA 2番号は既知制約（日本から着信不可）でデッドウェイト、AU トールフリーも日本から発信不可
        - リリースは取り消し不可。+1 833 はキュー（BasicQueue / escalation）の発信者番号だったため
          update-queue-outbound-caller-config で解除（OutboundFlowId は維持）後にリリース
        - AU 番号は AI フロー割当を disassociate-phone-number-contact-flow で解除後にリリース
      - 結果: 保有番号 0 件・トールフリー維持費停止。AI フロー本体は維持
      - アウトバウンド発信者番号は未設定（着信 AI には無影響。発信時のみ要再設定）
- [x] Lex v2 ja_JP ロケールビルド不能の修正（PR #78）:
      - 音声経路検証で RecognizeText が "The alias isn't built" で失敗。原因は ja_JP が
        AMAZON.FallbackIntent のみで、Lex v2 のビルド要件（カスタムインテント＋発話例 ≥1）未充足
      - shared_infra_stack.ts にカスタムインテント RagQuery（日本語発話例3つ）を追加。
        ASR パススルー設計（$.Lex.InputTranscript）は維持
- [x] Lex BotVersion イミュータブル問題の修正（PR #79）:
      - PR #78 デプロイ後も RecognizeText 失敗継続。AWS::Lex::BotVersion はイミュータブルで
        ボット変更時に新バージョンを作らず、エイリアス live が失敗スナップショット v1 を指したまま
      - CfnBotVersion の論理 ID をボットロケール定義の sha256 ハッシュ化（LexBotVersion<hash>）。
        変更時に新バージョン強制生成 → エイリアスが Fn::GetAtt で追従。AWS 推奨 Option 1 相当
      - デプロイ後検証（2026-06-27）: ボット v2 生成・ja_JP Built、エイリアス live → v2、
        RecognizeText 成功（Intent=RagQuery, State=ReadyForFulfillment）
- [x] 番号なしテスト導線の確立（個人アカウントのため日本番号取得は見送り）:
      - 日本番号は業務利用＋事業者書類3点が必須（AWS サポートケース経由）で個人アカウントは適格外
      - テストは Connect テストチャット / Lex RecognizeText / RagHandler 直接 invoke で番号なしに実施可能
      - RagHandler 直接 invoke で RAG コア動作を再確認（hit:true、じぶん銀行実ソース付き回答）
- [x] U-08 Web チャット（ストリーミング）新設（2026-06-28、PR #81-84）:
      - chat-api（FastAPI + Lambda Web Adapter, Function URL RESPONSE_STREAM）で
        既存 RAG パイプラインを Web 公開。SSE で sources→token*→done を逐次配信
      - BedrockClient.generate_answer_stream / sources_for 追加
      - chat-ui（独立 Vite/React）: fetch+ReadableStream で逐次表示・ソースリンク
      - run.sh の PYTHONPATH 修正（layer の uvicorn を自前 spawn python に解決, PR #83）
      - dev デプロイ・実機検証: /health ok、/chat ストリーミング・実回答を確認
- [x] RAG ベクトルキャッシュ整合性バグの恒久修正（PR #84）:
      - matrix.npy(129,861) と meta.json(129,863) のドリフトで RAG 全体が hit:false
      - patch の stale-index 重複バグ修正、書き込み前整合性ガード、Embedder 直列化
      - 一回限りフル再ビルド（130,213 件）で healing → RagHandler/chat-api とも hit:true 復帰
- [x] 品質・レイテンシ評価（scripts/rag_eval, Phase C/D）:
      - 当初の「ヒット率100%」はヒット有無のみで品質を測れておらず誤解を招く指標だった
      - LLM-as-judge（judge_eval.py, Claude Sonnet）で忠実性/有用性を実測し直し、
        検索診断（inspect_retrieval.py）で無関係文脈の混入を特定
      - MIN_HIT_SCORE 0.30→0.40 + プロンプト強化で改善（PR #88）:
        忠実性 4.14→4.71、有用性 3.79→3.57、的確(≥4/≥4) 57%→71%、ハルシネーション 3→0 件
      - 残 29% は捏造でなくコーパス欠落で安全に hedge（次策: headless クロール）
      - レイテンシ: ウォーム TTFT 中央値 ~1.5s、総時間中央値 ~4.2s
- [x] ポートフォリオ文書（Phase E）: PROJECT.md（概要・アーキ図・評価・デモ GIF）、rag_eval/README.md
- [x] chat-ui の Markdown レンダリング追加（react-markdown, PR #89）で回答表示を整形
- [x] ブラウザ CORS 二重 ACAO ヘッダ修正（FastAPI CORSMiddleware 除去, PR #87）
