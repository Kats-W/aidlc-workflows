# U-02 Knowledge Pipeline — Infrastructure Design Plan

## 計画メタデータ

- **ユニット**: U-02 Knowledge Pipeline
- **フェーズ**: Infrastructure Design
- **CDK スタック**: `KnowledgePipelineStack`（TypeScript / CDK v2）
- **リージョン**: ap-northeast-1
- **状態**: 確定済みコンテキストから全質問解決済み。

---

## 実行チェックリスト

- [x] Step 1: SSM 経由の SharedInfra 出力参照設計
- [x] Step 2: CrawlerLambda / EmbedderLambda の定義（runtime・mem・timeout）
- [x] Step 3: EventBridge Scheduler（週次）の設計
- [x] Step 4: 最小権限 IAM ロールの設計（"*" 排除）
- [x] Step 5: CloudWatch アラームの設計
- [x] Step 6: デプロイアーキテクチャ文書化

---

## 生成ドキュメント

| ファイル | 内容 |
| --- | --- |
| `infrastructure-design/infrastructure-design.md` | KnowledgePipelineStack 詳細 |
| `infrastructure-design/deployment-architecture.md` | デプロイフローと環境差分 |

---

## 主要な判断

1. スタック間依存は SSM Parameter Store 経由（CloudFormation Export の硬直化を回避）。
2. EmbedderLambda を先に定義し、Crawler の環境変数にその関数名を注入。
3. cron は UTC で記述（Sat 17:00 UTC = Sun 02:00 JST）。
