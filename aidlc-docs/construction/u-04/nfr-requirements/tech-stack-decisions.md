# U-04 Omnichannel & Escalation — Tech Stack Decisions

U-04 の技術スタック選定と根拠。

---

## TD-4.1 ランタイム: AWS Lambda Python 3.12

- **決定**: ChannelSwitchLambda は Python 3.12（256MB / 10 秒）。
- **根拠**: U-01〜U-03 と統一。低頻度・短時間のイベント処理に最適。
  Connect contact flow から直接同期呼び出しできる。

## TD-4.2 永続化: CustomerHistory 単一テーブル（SESSION# SK）

- **決定**: 新規テーブルを作らず、共通 CustomerHistory に `SESSION#<contactId>` SK を追加。
- **根拠**: 単一テーブル設計で TURN#/SUMMARY#/CSAT#/SESSION# を同一 PK（`customerId`）配下に集約。
  チャネル切り替え時に PK+SK 完全一致の `GetItem` で O(1) 取得でき、運用対象も増えない。
- **代替案**: 専用 SessionTable → 棄却（テーブル増による運用負荷・整合性管理コスト）。

## TD-4.3 シリアライズ: JSON 互換 Map/List（pickle 禁止）

- **決定**: `turns` を DynamoDB の L（List of M）で保存。
- **根拠**: `pickle` 禁止ルールの遵守、言語非依存、moto で検証容易。

## TD-4.4 ターン保持上限: 20

- **決定**: `MAX_TURNS = 20`、超過分は古いものから破棄。
- **根拠**: DynamoDB 400KB 項目上限と引き継ぎサマリーの十分性のトレードオフ。

## TD-4.5 非同期 I/O: asyncio.to_thread

- **決定**: boto3 同期呼び出しを `asyncio.to_thread` でラップ。
- **根拠**: U-03 と一貫した async ハンドラ。`lambda_handler` は `asyncio.run` で同期境界を提供。

## TD-4.6 エスカレーション: Connect TransferToQueue + SSM 解決の ARN

- **決定**: エスカレーションキュー ARN を SSM（`/au-jibun-bank/<env>/connect/escalation-queue-arn`）から解決し、
  CDK の出力として TransferToQueue 配線に供給。EscalationLambda は U-03 を再利用。
- **根拠**: ARN ハードコード禁止、Connect 管理者が払い出すキューと疎結合。

## TD-4.7 IaC: AWS CDK (TypeScript)

- **決定**: OmnichannelStack を CDK で定義し、SharedInfraStack の SSM エクスポートを消費。
- **根拠**: 既存スタック群（U-01〜U-03）と統一。Permission Boundary を全 Lambda ロールに付与。

## TD-4.8 観測性: aws-lambda-powertools Logger + CloudWatch Alarm

- **決定**: 構造化ログ + エラーアラーム。
- **根拠**: 既存ユニットと統一した監視体験。
