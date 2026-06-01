# Requirements Verification Questions
# au Jibun Bank AI Agent

**Instructions**: Fill in all `[Answer]:` tags below and return to the chat saying "回答を記入しました。続けてください。" (または英語でも可).

---

## Question 1: クローリング頻度
au じぶん銀行公式サイト・FAQ のクローリングをどの頻度で実行しますか？

A) 日次（深夜 1 回）— アクセス負荷が低く、ほぼリアルタイムな鮮度
B) 週次（毎週日曜深夜）— サーバー負荷最小化、FAQ 更新タイミングと一致
C) 混合（トップページ・お知らせは日次、FAQ・商品詳細ページは週次）
D) ほぼリアルタイム（変更検知型、ページ更新シグナルがあれば即時）
X) その他（[Answer]: の後に記入してください）

[Answer]: 

---

## Question 2: 顧客認証方式（ネイティブアプリ連携）
ネイティブアプリから Amazon Connect へのコンタクト開始時、顧客を識別・認証するために使用する仕組みは何ですか？

A) au ID トークン（JWT）を Amazon Connect StartChatContact API の attributes に渡し、Lambda でトークン検証
B) Amazon Connect の顧客プロファイルを使い、アプリが匿名で開始後に Lambda で顧客属性を付与
C) 非認証（匿名）で開始し、コンタクトフロー内でデバイス ID や端末識別子のみを使用
D) au じぶん銀行の既存 API Gateway + Cognito を介した認証フローに連携
X) その他（[Answer]: の後に記入してください）

[Answer]: 

---

## Question 3: エスカレーション後の AI 復帰
有人オペレーター対応後、同セッション内で再び AI エージェントへ引き戻すことを許容しますか？

A) はい — オペレーターが解決後、AI エージェントへシームレスに戻す（CSAT アンケートなど後続フロー）
B) いいえ — 有人対応が開始されたらそのセッション内では AI へ戻らない
C) 部分的 — CSAT アンケートと終了フローのみ AI が対応し、問い合わせ対応には戻らない
X) その他（[Answer]: の後に記入してください）

[Answer]: 

---

## Question 4: 音声処理アーキテクチャ
音声チャネルの ASR（音声認識）と TTS（音声合成）の実装方針は何ですか？

A) Amazon Lex v2 のネイティブ音声機能を使用（Lex が ASR + NLU を一体処理、Polly が TTS）
B) Amazon Transcribe で ASR → Lex v2 で NLU → Polly で TTS（コンポーネント分離）
C) Amazon Connect のネイティブ音声処理のみ使用（Lex 連携あり・Transcribeなし）
X) その他（[Answer]: の後に記入してください）

[Answer]: 

---

## Question 5: 会話履歴の保持期間
顧客の会話履歴（DynamoDB に保存するサマリー）の保持期間を指定してください。

A) 1 年間（短期間・コスト最小）
B) 3 年間（一般的な金融記録保持期間）
C) 5 年間（金融規制上の安全マージンを含む）
D) 7 年間以上（監査・コンプライアンス要件に対応した長期保持）
X) その他（[Answer]: の後に記入してください）

[Answer]: 

---

## Question 6: 管理ダッシュボードの実装方式
週次改善提案などを表示する管理ダッシュボードの実装をどうしますか？

A) Amazon CloudWatch ダッシュボード + Lambda API バックエンド（AWS ネイティブ、追加コスト最小）
B) Amazon QuickSight（可視化機能が豊富、追加コストあり）
C) AWS Amplify + React フロントエンド（カスタム UI、フルスタック）
D) 既存の社内管理ツール（Confluence、Notion 等）への週次レポート出力
X) その他（[Answer]: の後に記入してください）

[Answer]: 

---

## Question 7: 改善提案の通知先
週次で生成された FAQ・ウェブサイト改善提案をコンテンツ管理者へ通知する手段は？

A) メール通知（Amazon SES 経由）
B) Slack 通知（AWS Chatbot 経由）
C) 管理ダッシュボードのみ（通知なし・管理者が確認しに行く）
D) A と B の両方（メール + Slack）
X) その他（[Answer]: の後に記入してください）

[Answer]: 

---

## Question 8: Amazon Connect インスタンス
Amazon Connect インスタンスの状態を教えてください。

A) 既存インスタンスが存在する — そこへフロー・ボットを追加する形で実装
B) 新規インスタンスを作成する — CDK で完全新規プロビジョニング
C) まだ決まっていないが、CDK で新規作成を想定して設計してほしい
X) その他（[Answer]: の後に記入してください）

[Answer]: 

---

## Question 9: ナレッジベースの初期コンテンツ量見積もり
au じぶん銀行の対象サイトのページ数・FAQ 件数を大まかに教えてください（ベクトルDB のスケーリング見積もりに使用します）。

A) 小規模（ページ数 100 未満、FAQ 100 件未満）
B) 中規模（ページ数 100〜500、FAQ 100〜500 件）
C) 大規模（ページ数 500 以上、FAQ 500 件以上）
X) 不明（デフォルト見積もりで設計してください）

[Answer]: 

---

## Question 10: 多言語対応（将来）
バックログとして記載されている多言語対応について、設計時に拡張ポイントを考慮しますか？

A) はい — 多言語対応を将来追加しやすい拡張ポイントを設計に組み込む（ただし MVP では日本語のみ実装）
B) いいえ — MVP スコープに集中し、多言語対応の拡張ポイントは不要

[Answer]: 

---

## Question 11: Security Extension
Should security extension rules be enforced for this project?

A) Yes — enforce all SECURITY rules as blocking constraints (recommended for production-grade applications)
B) No — skip all SECURITY rules (suitable for PoCs, prototypes, and experimental projects)
X) Other (please describe after [Answer]: tag below)

[Answer]: 

---

## Question 12: Property-Based Testing Extension
Should property-based testing (PBT) rules be enforced for this project?

A) Yes — enforce all PBT rules as blocking constraints (recommended for projects with business logic, data transformations, serialization, or stateful components)
B) Partial — enforce PBT rules only for pure functions and serialization round-trips
C) No — skip all PBT rules
X) Other (please describe after [Answer]: tag below)

[Answer]: 
