# U-02 Knowledge Pipeline — NFR Requirements Plan

## 計画メタデータ
- **ユニット**: U-02 Knowledge Pipeline
- **フェーズ**: NFR Requirements
- **状態**: 確定済みコンテキストから全質問解決済み。

---

## 実行チェックリスト

- [x] Step 1: パフォーマンス制約の定義（検索 8 秒以内）
- [x] Step 2: コスト制約の定義（差分のみ埋め込み・On-Demand）
- [x] Step 3: セキュリティ要件の定義（pickle 禁止・最小権限・KMS）
- [x] Step 4: 信頼性要件の定義（リトライ・robots 遵守）
- [x] Step 5: 技術スタック決定の文書化

---

## 生成ドキュメント

| ファイル | 内容 |
| --- | --- |
| `nfr-requirements/nfr-requirements.md` | 8 秒制約・コスト・セキュリティ・信頼性 |
| `nfr-requirements/tech-stack-decisions.md` | ライブラリ選定とその根拠 |

---

## 主要な判断
1. RAG 検索のレイテンシ予算 8 秒のうち、ベクトル検索は /tmp キャッシュ利用で 200ms 以内を目標。
2. 差分埋め込みにより週次の Bedrock 呼び出しコストを変更分のみに限定。
