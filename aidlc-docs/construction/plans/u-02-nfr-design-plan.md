# U-02 Knowledge Pipeline — NFR Design Plan

## 計画メタデータ

- **ユニット**: U-02 Knowledge Pipeline
- **フェーズ**: NFR Design
- **状態**: 確定済みコンテキストから全質問解決済み。

---

## 実行チェックリスト

- [x] Step 1: 指数バックオフ + ジッタの設計
- [x] Step 2: /tmp キャッシュ（TTL 900s, .npy + JSON）の設計
- [x] Step 3: polite crawling（robots + ランダムディレイ）の設計
- [x] Step 4: 論理コンポーネント分解の文書化
- [x] Step 5: エラー → 型付き例外マッピングの設計

---

## 生成ドキュメント

| ファイル | 内容 |
| --- | --- |
| `nfr-design/nfr-design-patterns.md` | バックオフ・キャッシュ TTL・冪等性パターン |
| `nfr-design/logical-components.md` | コンポーネント分解と依存関係 |

---

## 主要な判断

1. リトライ可否は例外型の `retryable` 属性で表現し、呼び出し側が型でバックオフ制御。
2. キャッシュは 3 ファイル（行列 / メタ / timestamp）で TTL を独立管理。
