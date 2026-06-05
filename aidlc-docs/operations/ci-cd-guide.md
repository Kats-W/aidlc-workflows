# CI/CD Guide — au Jibun Bank AI Agent

## 概要

GitHub Actions + AWS OIDC による自動デプロイパイプライン。
アクセスキー不要。`main` へのプッシュで dev 環境へ自動デプロイ。

---

## パイプライン構成

```text
push to main
  ├── Markdown Lint          (並列)
  ├── Python CI              (並列) ruff → mypy → pytest
  └── CDK CI                 (並列) tsc → cdk synth
        └── CDK Deploy (dev) (直列) OIDC assume → bootstrap → deploy 7 stacks
```

デプロイ順序（依存関係に従い直列）:

```text
SharedInfra → KnowledgePipeline → Conversation → Omnichannel
           → Profile → Improvement → Dashboard
```

---

## 初期セットアップ（一度だけ）

### 1. AWS: OIDC プロバイダー登録

IAM → Identity providers → Add provider

| 項目          | 値                                            |
| ------------- | --------------------------------------------- |
| Provider type | OpenID Connect                                |
| Provider URL  | `https://token.actions.githubusercontent.com` |
| Audience      | `sts.amazonaws.com`                           |

### 2. AWS: IAM ロール作成

IAM → Roles → Create role → Web identity

**信頼ポリシー**:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:Kats-W/aidlc-workflows:*"
      }
    }
  }]
}
```

**権限ポリシー**: `AdministratorAccess`（CDK は多くの AWS サービスを操作するため）

**Permissions boundary**: 不要（OIDC 信頼ポリシーが制約として機能する）

### 3. GitHub: Secret 登録

Settings → Secrets and variables → Actions → New repository secret

| Name           | Value                                          |
| -------------- | -------------------------------------------- |
| `AWS_ROLE_ARN` | `arn:aws:iam::<ACCOUNT_ID>:role/<ROLE_NAME>` |

---

## 開発ワークフロー

```bash
# ローカルで開発・テスト
uv run pytest tests/unit -q
uv run ruff check src/ tests/
uv run mypy src/

# main にマージ → Actions が自動で dev 環境へデプロイ
git push origin main
```

Actions の進捗: GitHub → Actions タブでリアルタイム確認。

---

## staging / prod デプロイ（手動）

現在 `ci.yml` に staging/prod ジョブは未定義。追加時は GitHub Environments の
`required reviewers` を設定して手動承認ゲートを設ける。

```yaml
# staging/prod ジョブ追加例
cdk-deploy-staging:
  needs: cdk-deploy-dev
  environment: staging          # GitHub Environment で承認者を設定
  if: github.ref == 'refs/heads/main'
  steps:
    - run: npx cdk deploy "AuJibunBank-staging-*" --context env=staging --require-approval never
```

---

## トラブルシューティング

**症状**: `AssumeRoleWithWebIdentity` 403

- **原因**: OIDC プロバイダー未登録 or trust policy の `sub` 条件ミス
- **対処**: AWS IAM で OIDC プロバイダーと trust policy を確認

**症状**: `cdk bootstrap` 失敗

- **原因**: IAM ロールの権限不足
- **対処**: `AdministratorAccess` が付与されているか確認

**症状**: `cdk deploy` タイムアウト

- **原因**: Lambda Docker バンドルが遅い
- **対処**: Actions のタイムアウト上限（6h）内なら正常

**症状**: `cdk synth` は通るが deploy 失敗

- **原因**: SSM パラメータ不足（前段スタック未デプロイ）
- **対処**: SharedInfra から順番にデプロイされているか確認
