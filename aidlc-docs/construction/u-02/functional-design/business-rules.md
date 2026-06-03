# U-02 Business Rules — Knowledge Pipeline

---

## BR-1. robots.txt 遵守ルール

| ID | ルール |
| --- | --- |
| BR-1.1 | クロールは必ず対象ホストの `robots.txt` をロードしてから実施する。 |
| BR-1.2 | HTTP `User-Agent` ヘッダは `AuJibunBankBot/1.0` を送信する。 |
| BR-1.3 | robots.txt のグループ照合にはバージョンを除いた製品トークン `AuJibunBankBot` を使う（CPython `RobotFileParser` は `/` 以降を無視するため）。 |
| BR-1.4 | robots.txt が 404 の場合のみ「全許可」とみなす。 |
| BR-1.5 | robots.txt が 5xx・接続失敗・タイムアウトの場合は `FetchTimeoutError`（retryable）を送出する。 |
| BR-1.6 | `RobotsTxtGuard.load()` 未実行の状態では `is_allowed()` は常に False を返す（fail-safe deny）。 |
| BR-1.7 | 各 URL 取得後に 1〜3 秒のランダムディレイ（`asyncio.sleep`）を挿入する（polite crawling）。 |

---

## BR-2. チャンク設計ルール

| ID | ルール |
| --- | --- |
| BR-2.1 | 本文抽出時に `script` / `style` / `noscript` / `nav` / `footer` / `header` 要素を除去する。 |
| BR-2.2 | 連続する空白（半角・全角・改行）は単一の半角スペースに正規化する。 |
| BR-2.3 | チャンク最大長は **1500 文字**。 |
| BR-2.4 | チャンク間オーバーラップは **200 文字**（文脈断絶を防ぐ）。オーバーラップは最大長未満であること。 |
| BR-2.5 | チャンク ID は `"{sha256(source_url)}#{index}"`。 |
| BR-2.6 | チャンクの `content_hash` は本文の SHA-256 16 進。 |
| BR-2.7 | 抽出本文が空のページは `ParseError` を送出する（埋め込み対象にしない）。 |

---

## BR-3. 差分・更新ルール

| ID | ルール |
| --- | --- |
| BR-3.1 | 差分判定はチャンク単位の `content_hash` 比較で行う（ページ単位ではない）。 |
| BR-3.2 | ContentDiff に無い → `added`、ハッシュ相違 → `changed`、ContentDiff にあり新規クロールに無い → `deleted`。 |
| BR-3.3 | 差分が空（added/changed/deleted いずれも 0）の場合 EmbedderLambda を呼び出さない（コスト削減）。 |
| BR-3.4 | `diff` は冪等：同一クロールを再適用しても 2 回目は空差分になる。 |
| BR-3.5 | embedding は DynamoDB 数値型制約により `Decimal` リストとして格納する（float 直接不可）。 |

---

## BR-4. /tmp キャッシュルール

| ID | ルール |
| --- | --- |
| BR-4.1 | キャッシュ TTL は **900 秒（15 分）**。 |
| BR-4.2 | キャッシュは `/tmp/vectors.npy`（行列）+ `/tmp/vectors_meta.json`（メタ）+ `/tmp/vectors_ts.txt`（timestamp）の 3 ファイル。 |
| BR-4.3 | **pickle は使用禁止**。numpy `.npy` と JSON のみを使用する。 |
| BR-4.4 | 3 ファイルが揃い、かつ `now - ts < 900` の場合のみキャッシュを有効とみなす。 |
| BR-4.5 | キャッシュロード失敗（破損・欠損）時は DynamoDB から再構築しキャッシュを上書きする。 |
| BR-4.6 | キャッシュ書き込み失敗は検索を中断させない（警告ログのみ）。 |

---

## BR-5. 検索ルール

| ID | ルール |
| --- | --- |
| BR-5.1 | クエリベクトルが空の場合 `SearchError` を送出する。 |
| BR-5.2 | クエリ次元とコーパス次元が不一致の場合 `SearchError` を送出する。 |
| BR-5.3 | コーパスが空の場合は空リストを返す（エラーにしない）。 |
| BR-5.4 | ノルム 0 の行はスコア 0 とする（ゼロ除算回避）。 |
| BR-5.5 | 結果はコサインスコア降順、既定 `top_k = 5`。 |
