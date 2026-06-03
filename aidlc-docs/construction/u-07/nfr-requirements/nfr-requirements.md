# U-07 Admin Dashboard — NFR Requirements

## 1. パフォーマンス
| ID | 要件 |
|---|---|
| PERF-01 | `GET /suggestions` は p95 < 1.5s（1週分・最大100件想定）。 |
| PERF-02 | `GET /metrics` の 7日/30日切替後の画面反映は 3秒以内（US-7.2）。 |
| PERF-03 | DashboardApiLambda 256MB/30s、MetricsAggregatorLambda 256MB/60s に収める。 |
| PERF-04 | 提案一覧のページングはサーバー側で完結し、1ページ既定10件。 |

## 2. セキュリティ
| ID | 要件 |
|---|---|
| SEC-01 | ダッシュボード API は Cognito JWT オーソライザーで保護（US-7.3）。匿名アクセス不可。 |
| SEC-02 | Cognito UserPool は MFA OPTIONAL（TOTP）、パスワード min8/大文字/小文字/数字/記号。 |
| SEC-03 | サインイン失敗 5回でアカウントロックアウト相当（Advanced Security / 試行制限）。 |
| SEC-04 | UserPool Client は SPA 用（client secret なし）。 |
| SEC-05 | CSV エクスポートは CSV インジェクション（`= + - @` 始まり）をクォートで無害化。 |
| SEC-06 | DynamoDB は共有 CMK で暗号化。IAM は最小権限（`*` アクション禁止、テーブル/index ARN にスコープ）。 |
| SEC-07 | レスポンスはレビュー担当者向け管理データのみ。生会話テキスト・未マスク PII を返さない。 |

## 3. 可用性 / 信頼性
| ID | 要件 |
|---|---|
| REL-01 | MetricsAggregator は 0件データでもエラーにせず 0/null を返す。 |
| REL-02 | PATCH は存在チェック付き条件更新で冪等性を担保（存在しない id は 404）。 |
| REL-03 | API トークン期限切れ（401）はクライアント側で1回自動リフレッシュ・再試行。 |
| REL-04 | CloudWatch エラーアラームを両 Lambda に設定。 |

## 4. 運用 / 観測性
| ID | 要件 |
|---|---|
| OPS-01 | aws-lambda-powertools Logger による構造化ログ。 |
| OPS-02 | EventBridge Scheduler で週次（日曜 18:30 UTC）に MetricsAggregator を事前計算。 |
| OPS-03 | SSM に UserPool ID / Client ID / API エンドポイント / Amplify App ID を出力。 |

## 5. 保守性 / 品質
| ID | 要件 |
|---|---|
| QUAL-01 | Python 3.12 型スタイル（`x | None`、`Optional` 禁止）。`pickle` 禁止。 |
| QUAL-02 | ユニットテストカバレッジ 80% 以上。0件境界・ページング境界（PBT）必須。 |
| QUAL-03 | フロントは `data-testid` 属性で E2E テスト可能にする。 |
| QUAL-04 | CDK TypeScript は `tsc --noEmit` クリーン。 |
