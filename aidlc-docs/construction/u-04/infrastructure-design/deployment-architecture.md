# U-04 Omnichannel & Escalation — Deployment Architecture

OmnichannelStack のデプロイ構成・順序・配線・ロールバック。

---

## 1. スタック依存関係

```
SharedInfraStack (U-01)
  ├─ CustomerHistory table 名 / KMS CMK ARN / Permission Boundary ARN / Connect キュー ARN を SSM へ公開
  ▼
OmnichannelStack (U-04)
  └─ SSM から上記を解決 → ChannelSwitchLambda + IAM + 監視
```

- SharedInfraStack が先にデプロイされ、SSM パラメータが存在することが前提。
- OmnichannelStack は ConversationStack（U-03）と並行デプロイ可能（直接依存なし。
  CustomerHistory テーブルを共用するのみ）。

---

## 2. CDK アプリ登録（infra/bin/app.ts）

```ts
new OmnichannelStack(app, `AuJibunBank-${env}-Omnichannel`, {
  env: cdkEnv,
  envName: env,
  description: `au Jibun Bank AI Agent — Omnichannel & Escalation (U-04) (${env})`,
});
```

- `env` コンテキスト（dev | staging | prod、既定 dev）でスタック名と SSM パスを切替。

---

## 3. デプロイ順序

1. `SharedInfraStack`（テーブル・KMS・境界・キュー ARN を SSM 公開）
2. Connect 管理者がエスカレーションキューを払い出し、ARN を SSM へ登録
3. `OmnichannelStack`（`cdk deploy AuJibunBank-<env>-Omnichannel`）
4. Connect contact flow を更新（ChannelSwitchLambda 呼び出し + TransferToQueue 配線）

---

## 4. Connect contact flow 配線

| ブロック | 役割 |
| --- | --- |
| Invoke AWS Lambda → ChannelSwitchLambda | チャネル切り替え時に `handover_summary` を取得 |
| Set contact attributes | `handover_summary` を次チャネルのプロンプトへ注入 |
| Transfer to queue | `EscalationQueueArn` を参照して有人転送（US-4.3） |

---

## 5. デプロイ環境

| 環境 | スタック名 | 備考 |
| --- | --- | --- |
| dev | `AuJibunBank-dev-Omnichannel` | 検証 |
| staging | `AuJibunBank-staging-Omnichannel` | 受け入れ |
| prod | `AuJibunBank-prod-Omnichannel` | 本番 |

---

## 6. ロールバック

- CloudFormation 変更セット失敗時は自動ロールバック。
- アプリ不具合時は Lambda の前バージョンへエイリアス切替（または `cdk deploy` で前リビジョンを再適用）。
- `SESSION#` データはテーブル共用のためスタック削除では消えない（TTL で自然失効）。

---

## 7. パッケージング

- `lambda.Code.fromAsset('..')` でリポジトリルートをバンドル（`infra`/`tests`/`aidlc-docs`/`.git`/`.venv` を除外）。
- CI では `uv` で依存を解決してデプロイパッケージを構築。
