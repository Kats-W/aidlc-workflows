# U-02 Knowledge Pipeline — Functional Design Plan

# au Jibun Bank AI Agent

## 計画メタデータ

- **ユニット**: U-02 Knowledge Pipeline
- **フェーズ**: Functional Design
- **割り当て User Story**: US-2.1（週次クロール）, US-2.2（差分更新）, US-2.3（ベクトル検索）
- **リージョン**: ap-northeast-1
- **状態**: 確定済みコンテキストから全質問解決済み。追加質問なし。

---

## ストーリートレーサビリティ

| User Story | 説明 | 実装コンポーネント |
| --- | --- | --- |
| US-2.1 | au じぶん銀行公式サイト・FAQ を週次クロール | `RobotsTxtGuard`, `ContentParser`, `CrawlerLambda`, EventBridge Scheduler |
| US-2.2 | 差分のみ Titan v2 でベクトル化し VectorStore に反映 | `DifferEngine`, `S3ContentStore`, `EmbedderLambda`, `VectorStore` |
| US-2.3 | コサイン類似度検索（/tmp キャッシュ付き）を提供 | `CosineSimilaritySearcher`, `BedrockClient.embed` |

---

## 実行チェックリスト

### フェーズ A: ドメインモデリング

- [x] Step A1: クロールフロー（robots → fetch → parse → chunk → S3）の定義
- [x] Step A2: 差分検出ロジック（added / changed / deleted）の定義
- [x] Step A3: コサイン類似度検索 + /tmp キャッシュ設計の定義

### フェーズ B: ビジネスルール

- [x] Step B1: robots.txt 遵守ルール（User-Agent / fail-safe deny）
- [x] Step B2: チャンク設計（1500 文字・200 文字オーバーラップ）
- [x] Step B3: キャッシュルール（TTL 900 秒・.npy + JSON・pickle 禁止）

### フェーズ C: ドメインエンティティ

- [x] Step C1: `ContentChunk` 定義
- [x] Step C2: `DiffResult` 定義
- [x] Step C3: `SearchHit` 定義

### フェーズ D: 確認

- [x] Step D1: 3 ストーリーのトレーサビリティ確認
- [x] Step D2: 禁止事項（pickle / requests / scrapy）との整合確認

---

## 生成ドキュメント

| ファイル | 内容 |
| --- | --- |
| `functional-design/business-logic-model.md` | クロール・差分・検索フロー |
| `functional-design/business-rules.md` | robots / チャンク / キャッシュルール |
| `functional-design/domain-entities.md` | ContentChunk / DiffResult / SearchHit |

---

## 主要な設計判断

1. **差分の最小単位はチャンク**: ページ単位ではなくチャンク単位で SHA-256 を比較し、再埋め込みコストを最小化。
2. **fail-safe deny**: robots.txt 未ロード時は全 URL を拒否。
3. **pickle 全面禁止**: /tmp キャッシュは numpy `.npy` + JSON で実装。
