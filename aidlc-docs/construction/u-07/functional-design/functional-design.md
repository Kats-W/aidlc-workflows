# U-07 Admin Dashboard — Functional Design

## 1. Scope

U-07 は au Jibun Bank AI Agent の「管理者ダッシュボード」を担当する。週次で生成された改善提案
（U-06 由来）をレビュー担当者が確認・承認/却下/保留し、利用統計を可視化する。担当ユーザー
ストーリーは次の3件。

| Story | 概要 |
|---|---|
| US-7.1 | 週次改善提案の確認と承認（提案一覧閲覧・承認/却下/保留、CSV エクスポート） |
| US-7.2 | 利用統計ダッシュボードの閲覧（Recharts グラフ、7日/30日切替） |
| US-7.3 | ダッシュボードへの認証アクセス（Cognito、MFA TOTP オプション、トークン自動リフレッシュ） |

## 2. Components

| Component | File | 種別 |
|---|---|---|
| DashboardApiLambda | `src/dashboard_api/handler.py` | API Gateway (HTTP API) バックエンド Lambda |
| MetricsAggregatorLambda | `src/dashboard_api/metrics_aggregator.py` | 集計 Lambda（同期 invoke + 週次スケジュール） |
| React SPA | `frontend/` | Amplify ホスティングの管理画面 |
| ApiClient | `frontend/src/api/ApiClient.ts` | Cognito ID トークン付与 + 自動リフレッシュ |
| SuggestionListView | `frontend/src/views/SuggestionListView.tsx` | 提案一覧 + ステータス操作 + CSV |
| MetricsView | `frontend/src/views/MetricsView.tsx` | Recharts グラフ + 期間切替 |
| SuggestionStatusControl | `frontend/src/components/SuggestionStatusControl.tsx` | 承認/却下/保留 UI |

## 3. Domain Entities

### 3.1 ImprovementSuggestion（DynamoDB: ImprovementSuggestions）
- PK=`suggestionId`（uuid4）。GSI `gsi_week`（PK=`weekStart`）/ `gsi_status`（PK=`status`, SK=`priorityScore`）。
- 属性: `status`(`pending`/`approved`/`rejected`/`hold`)、`weekStart`(ISO週 `2026-W23`)、`targetUrl`、
  `improvementText`(≤200字)、`priorityScore`、`createdAt`、`updatedAt`、`rejectReason`(却下時のみ)、`ttl`。

### 3.2 Metrics（CustomerHistory 由来の派生集計）
- `CustomerHistory` の `SUMMARY#{contactId}` / コンタクト属性から期間（7d/30d）で集計。
- 形: `{period, contacts:{total, voice, chat}, escalationRate, avgCsat, avgTurns, aiResolutionRate}`。
- `avgCsat` は CSAT データが1件もなければ `null`。0件期間は全メトリクスを 0/null で返す。

## 4. Business Logic Model

### 4.1 DashboardApiLambda.handler(event) — ルーティング (US-7.1, US-7.2)
API Gateway HTTP API のプロキシ統合。`event.requestContext.http.method` / `.path` でルーティング。
Cognito JWT 認可は HTTP API の JWT オーソライザーが担当するため、Lambda 側でのトークン再検証は不要。

| メソッド/パス | 処理 |
|---|---|
| `GET /suggestions?week=&page=&limit=` | `gsi_week` Query、priorityScore 降順、ページング |
| `PATCH /suggestions/{id}` | status 更新（approved/rejected/hold のみ）、却下時 rejectReason |
| `GET /metrics?period=7d\|30d` | MetricsAggregatorLambda を同期 invoke |
| `GET /suggestions/csv?week=` | 指定週の全提案を CSV テキストで返却 |

#### 4.1.1 GET /suggestions
1. `week` 未指定時は `current_week_label(now)`（`2026-W23` 形式、ISO週）。
2. `gsi_week`（PK=`weekStart`）で Query。最大12週遡れる（過去11週 + 現在週）。
3. `priorityScore` 降順ソート。
4. `page`（1始まり）/`limit`（既定10）でメモリページング。
5. レスポンス: `{suggestions:[...], total, page, totalPages}`。`totalPages = ceil(total/limit)`（total=0 → 0）。

#### 4.1.2 PATCH /suggestions/{id}
1. body の `status` を検証。`approved`/`rejected`/`hold` 以外は `ValidationError` → 400。
2. `rejected` の場合 `rejectReason` を併せて保存（任意）。
3. `UpdateItem`（`ConditionExpression=attribute_exists(suggestionId)`）。存在しない → `NotFoundError` → 404。
4. `status`、`updatedAt`、（rejected 時）`rejectReason` を更新。
5. レスポンス: `{suggestionId, status, updatedAt}`。

#### 4.1.3 GET /metrics
1. `period` を `7d`/`30d` に正規化（不正値は `ValidationError`）。
2. `MetricsAggregatorLambda` を同期 invoke（`period_days` を渡す）。
3. レスポンスをそのまま中継。

#### 4.1.4 GET /suggestions/csv
1. `week`（未指定時は現在週）で `gsi_week` Query（全件）。
2. ヘッダ `suggestionId,targetUrl,improvementText,priorityScore,status,createdAt`。
3. CSV インジェクション対策（`=+-@` 始まりはクォート）。`Content-Type: text/csv; charset=utf-8`。

#### 4.1.5 エラーマッピング
| 例外 | HTTP |
|---|---|
| `NotFoundError` | 404 |
| `ValidationError` | 400 |
| `UnauthorizedError` | 403 |
| その他 `AppError` / 未知 | 500 |

### 4.2 MetricsAggregatorLambda.aggregate_metrics(period_days) (US-7.2)
1. `window = [now - period_days, now)`。
2. `CustomerHistory` から期間内のコンタクト集計（`scan` + フィルタ、または GSI Query）。
3. 集計値:
   - `contacts.total` / `contacts.voice` / `contacts.chat`（チャネル別カウント）。
   - `escalationRate = escalated件数 / total`（total=0 → 0.0）。
   - `avgCsat = mean(csat)`（CSAT データ0件 → `None`）。
   - `avgTurns = mean(turns)`（0件 → 0.0）。
   - `aiResolutionRate = (total - escalated) / total`（0件 → 0.0）。
4. 0件データ時は全メトリクスを 0/null で返し、エラーにしない。

## 5. Frontend Behaviour

### 5.1 App（US-7.3）
- Amplify `withAuthenticator` HOC でラップ。未認証はサインイン画面、MFA TOTP は任意。
- `SuggestionListView` / `MetricsView` をタブ切替。

### 5.2 ApiClient（US-7.3）
- Cognito ID トークンを `Authorization` ヘッダーに付与。
- 401 → `fetchAuthSession({forceRefresh:true})` でトークン更新 → 1回だけ再試行。失敗時は再ログイン誘導。

### 5.3 SuggestionListView（US-7.1）
- 週セレクタ（現在週 + 過去11週 = 最大12週）、priorityScore 降順表示。
- `SuggestionStatusControl` で承認/却下/保留。CSV エクスポート、prev/next ページング。

### 5.4 MetricsView（US-7.2）
- 7日/30日切替、3秒以内表示。Recharts `ResponsiveContainer` + BarChart（コンタクト）/ LineChart（エスカレーション率）。

## 6. 非対象 / 前提
- 改善提案の生成は U-06 が担当（本ユニットは閲覧・状態更新のみ）。
- メトリクス元データ（CustomerHistory）は U-03/U-05 が書き込む。
- Amplify の実ビルド/デプロイは CI/CD が実施。CDK は設定パラメータのみ出力。
