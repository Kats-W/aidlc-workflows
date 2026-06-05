# U-07 Admin Dashboard — NFR Design

## 1. パフォーマンス設計

- **PERF-01/04**: `GET /suggestions` は `gsi_week`（PK=`weekStart`）で1週分のみ取得し、メモリ内で
  `priorityScore` 降順ソート → `page/limit` でスライス。1週の提案件数は U-06 上限により小さい
  （最大数十件）ためメモリページングで十分。
- **PERF-02**: メトリクスは週次スケジュールで MetricsAggregator が事前集計し得る構成。オンデマンド
  経路でも 256MB/60s 内で集計完了。フロントは切替時にローディング表示し、3秒以内に描画。
- **PERF-03**: Lambda メモリ/タイムアウトは要件どおり（API=256MB/30s、Aggregator=256MB/60s）。

## 2. セキュリティ設計

- **SEC-01/04**: HTTP API に Cognito JWT オーソライザー（`HttpJwtAuthorizer`）を付与。UserPool Client は
  `generateSecret=false`（SPA）。Lambda は認可済み前提で JWT 再検証しない。
- **SEC-02/03**: UserPool は `mfa: OPTIONAL` + TOTP、`passwordPolicy`（min8/大小英字/数字/記号）、
  `AdvancedSecurityMode.ENFORCED` 相当でブルートフォースを抑止（試行制限 5）。
- **SEC-05**: CSV 生成時、フィールドが `= + - @` で始まる場合は先頭にシングルクォートを付け、
  ダブルクォートで囲み（`csv` モジュールの quoting + 明示エスケープ）。
- **SEC-06**: Lambda 実行ロールは共有 permission boundary 配下。DynamoDB は対象テーブル/index ARN に
  限定（`Query`/`GetItem`/`UpdateItem`）。CMK は `grantEncryptDecrypt`。`lambda:InvokeFunction` は
  Aggregator ARN に限定。
- **SEC-07**: API レスポンスは ImprovementSuggestion の管理属性と集計済みメトリクスのみ。

## 3. 信頼性設計

- **REL-01**: `aggregate_metrics` は集計対象0件時に総数0・`avgCsat=None`・他レートを0.0で返す分岐を持つ。
- **REL-02**: PATCH は `UpdateItem` + `ConditionExpression=attribute_exists(suggestionId)`。
  `ConditionalCheckFailedException` を `NotFoundError` に変換。
- **REL-03**: ApiClient は 401 受信時 `fetchAuthSession({forceRefresh:true})` で1回だけ再試行。
  再試行も失敗なら例外を投げ UI が再ログインを促す。
- **REL-04**: 両 Lambda に CloudWatch `metricErrors` アラーム（1時間で1件以上）。

## 4. 観測性設計

- **OPS-01**: `POWERTOOLS_SERVICE_NAME=u-07-dashboard`、`LOG_LEVEL=INFO`。
- **OPS-02**: EventBridge Scheduler `cron(30 18 ? * SUN *)` UTC で MetricsAggregator を週次実行。
- **OPS-03**: `CfnOutput` + SSM パラメータで UserPool ID / Client ID / API endpoint / Amplify App ID を出力。

## 5. 品質設計

- **QUAL-01/02**: 全モジュール `from __future__ import annotations` + `x | None`。テストは
  moto[dynamodb] でテーブルをモック。0件境界（metrics）と `total/limit→totalPages` 整合性を
  hypothesis PBT で検証。
- **QUAL-03**: 仕様で定めた `data-testid` を全インタラクティブ要素に付与。
- **QUAL-04**: `infra/lib/stacks/dashboard_stack.ts` は既存 stack と同一の CDK v2 パターン。
