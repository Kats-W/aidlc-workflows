# Component Methods — au Jibun Bank AI Agent

本ドキュメントは各コンポーネントの主要メソッドのシグネチャ（Python 3.12 型アノテーション）・引数/戻り値の型・想定例外を定義する。詳細なビジネスロジック実装は Functional Design フェーズで記述する。ここではシグネチャと概要のみを示す。

- 型スタイル: `list[dict]`, `str | None`, `dict[str, Any]` 等の Python 3.12 スタイル。
- I/O を伴う処理は原則 `async def`（`aioboto3` / `asyncio` 想定）。CPU バウンドな純粋計算（コサイン類似度等）は同期メソッド。
- 命名: クラスは UpperCamelCase、メソッドは snake_case。
- 共通例外は本文末尾の「共通例外型」を参照。

---

## 0. 共通型エイリアス・データクラス

```python
from dataclasses import dataclass
from typing import Any, TypedDict

type Vector = list[float]               # 埋め込みベクトル（Titan v2: 1024 次元）
type JsonDict = dict[str, Any]

@dataclass(frozen=True)
class ContentChunk:
    chunk_id: str
    source_url: str
    text: str
    content_hash: str          # SHA-256（差分検出用）
    lang: str = "ja"
    crawled_at: str = ""       # ISO8601

@dataclass(frozen=True)
class DiffResult:
    added: list[ContentChunk]
    changed: list[ContentChunk]
    deleted: list[str]         # 削除された chunk_id

@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    text: str
    source_url: str
    score: float               # コサイン類似度 [0.0, 1.0]

@dataclass(frozen=True)
class RagAnswer:
    answer: str
    sources: list[str]         # 出典 URL
    confidence: float
    hit: bool                  # ナレッジヒット有無（False ならエスカレーション候補）

@dataclass(frozen=True)
class ConversationTurn:
    role: str                  # "user" | "assistant"
    text: str                  # PII マスク済み
    timestamp: str

class ConnectEvent(TypedDict):
    Details: JsonDict
    Name: str
```

---

## 1. U-02: Knowledge Pipeline

### CrawlerLambda

```python
class CrawlerLambda:
    async def handler(self, event: JsonDict, context: object) -> JsonDict:
        """EventBridge Scheduler エントリポイント。クロール → S3 保存 → 差分検出をオーケストレーション。
        戻り値: {"crawled": int, "added": int, "changed": int, "deleted": int, "errors": list[str]}
        例外: CrawlerError（致命的失敗時、DLQ 送出）"""

    async def crawl_site(self, base_url: str) -> list[ContentChunk]:
        """robots.txt 遵守・1〜3 秒ディレイで対象サイトを巡回しチャンク列を返す。
        例外: RobotsDisallowedError, FetchTimeoutError"""
```

### RobotsTxtGuard

```python
class RobotsTxtGuard:
    async def load(self, base_url: str) -> None:
        """robots.txt を取得・解析。例外: FetchTimeoutError"""

    def is_allowed(self, url: str) -> bool:
        """Disallow ルール照合。未ロード時は RobotsNotLoadedError"""
```

### ContentParser

```python
class ContentParser:
    def parse(self, raw_html: str, source_url: str) -> list[ContentChunk]:
        """HTML 本文抽出・正規化・チャンク分割（最大トークン長で分割）。
        例外: ParseError（本文抽出不能時）"""

    def compute_hash(self, text: str) -> str:
        """チャンク本文の SHA-256 ハッシュを返す。"""
```

### DifferEngine

```python
class DifferEngine:
    async def diff(self, new_chunks: list[ContentChunk]) -> DiffResult:
        """ContentDiff テーブルの前回ハッシュ集合と比較し added/changed/deleted を判定。
        例外: DynamoAccessError"""

    async def commit(self, result: DiffResult) -> None:
        """ContentDiff テーブルへ最新ハッシュ状態を upsert / 削除分を除去。
        例外: DynamoAccessError"""
```

### EmbedderLambda

```python
class EmbedderLambda:
    async def handler(self, event: JsonDict, context: object) -> JsonDict:
        """DiffResult（S3 参照）を受領し、added/changed をベクトル化して upsert、deleted を除外。
        戻り値: {"upserted": int, "deleted": int}
        例外: EmbeddingError, DynamoAccessError"""

    async def embed_and_upsert(self, chunks: list[ContentChunk]) -> int:
        """Titan Embeddings v2 でベクトル化し VectorStore へ upsert。upsert 件数を返す。"""
```

### VectorStore

```python
class VectorStore:
    async def upsert(self, chunk: ContentChunk, vector: Vector) -> None:
        """VectorStore テーブルへ 1 チャンクを upsert。例外: DynamoAccessError"""

    async def delete(self, chunk_id: str) -> None:
        """chunk_id を削除。例外: DynamoAccessError"""

    async def scan_all(self, projection: list[str] | None = None) -> list[JsonDict]:
        """全件スキャン（コサイン検索用に embedding / text / sourceUrl を投影）。
        例外: DynamoAccessError"""
```

### CosineSimilaritySearcher

```python
class CosineSimilaritySearcher:
    def __init__(self, cache_ttl_seconds: int = 900) -> None: ...

    async def search(self, query_vec: Vector, top_k: int = 5) -> list[SearchHit]:
        """/tmp キャッシュ（15 分 TTL）を利用しコサイン類似度で上位 top_k を返す。
        キャッシュ失効時は VectorStore.scan_all() で再構築。
        例外: SearchError"""

    def _cosine(self, a: Vector, b: Vector) -> float:
        """2 ベクトル間のコサイン類似度（同期・純粋計算）。"""

    def _is_cache_valid(self) -> bool:
        """/tmp キャッシュの鮮度判定。"""
```

### S3ContentStore

```python
class S3ContentStore:
    async def put(self, key: str, body: str) -> str:
        """S3 へ本文保存しオブジェクトキーを返す。例外: S3AccessError"""

    async def get(self, key: str) -> str:
        """S3 から本文取得。例外: S3AccessError, ObjectNotFoundError"""

    async def delete(self, key: str) -> None:
        """S3 オブジェクト削除。例外: S3AccessError"""
```

---

## 2. U-03: AI Conversation Engine

### RagHandlerLambda

```python
class RagHandlerLambda:
    async def handler(self, event: ConnectEvent, context: object) -> JsonDict:
        """Connect コンタクトフローフック。8 秒以内に応答。
        フロー: PII マスク → 履歴サマリー注入 → ベクトル検索 → Claude 回答生成 → 出典付与 → 履歴保存。
        戻り値: {"answer": str, "sources": list[str], "hit": bool}
        例外: BedrockError, TimeoutBudgetExceeded（タイムアウト予算超過時はフォールバック応答）"""

    async def answer(self, customer_id: str, user_input: str) -> RagAnswer:
        """RAG パイプライン本体。例外: BedrockError, SearchError"""
```

### BedrockClient

```python
class BedrockClient:
    async def generate_answer(
        self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.2
    ) -> str:
        """Claude claude-sonnet-4-6 で回答生成。
        例外: BedrockThrottledError, BedrockError"""

    async def embed(self, text: str) -> Vector:
        """Titan Embeddings v2 で 1024 次元ベクトル生成。例外: EmbeddingError"""

    async def analyze_gap(self, prompt: str) -> JsonDict:
        """Claude でナレッジギャップ分析（JSON 出力）。例外: BedrockError, ResponseParseError"""
```

### PiiMasker

```python
class PiiMasker:
    async def mask(self, text: str, lang: str = "ja") -> tuple[str, list[JsonDict]]:
        """Amazon Comprehend で PII 検出しマスク。
        戻り値: (マスク済みテキスト, 検出エンティティリスト)
        例外: ComprehendError"""

    def contains_pii(self, entities: list[JsonDict]) -> bool:
        """検出エンティティに PII が含まれるか（ログ出力可否判定用）。"""
```

### PersonalizerLambda

```python
class PersonalizerLambda:
    async def handler(self, event: JsonDict, context: object) -> JsonDict:
        """customer_id を受領し直近 5 件の会話サマリーテキストを返す。
        戻り値: {"summary": str, "turn_count": int}"""

    async def build_context(self, customer_id: str, limit: int = 5) -> str:
        """履歴サマリーをプロンプト用テキストへ整形。例外: DynamoAccessError"""
```

### HistoryRepository

```python
class HistoryRepository:
    async def append_turn(self, customer_id: str, turn: ConversationTurn) -> None:
        """CustomerHistory テーブルへターン追記（TTL 90 日）。turn.text は PII マスク済み前提。
        例外: DynamoAccessError"""

    async def get_recent(self, customer_id: str, limit: int = 5) -> list[ConversationTurn]:
        """直近 limit 件のターンを新しい順に取得。例外: DynamoAccessError"""

    async def save_summary(self, customer_id: str, summary: str) -> None:
        """会話サマリーを保存。例外: DynamoAccessError"""
```

### CsatHandlerLambda

```python
class CsatHandlerLambda:
    async def handler(self, event: ConnectEvent, context: object) -> JsonDict:
        """コンタクト終了時の CSAT スコアを受領し記録。
        戻り値: {"saved": bool, "contact_id": str, "score": int}
        例外: ValidationError（スコア範囲外）, DynamoAccessError"""

    async def record(self, contact_id: str, customer_id: str, score: int) -> None:
        """CSAT を CustomerHistory（または専用パーティション）へ保存。score は 1..5。"""
```

---

## 3. U-04: Omnichannel & Escalation

### ChannelSwitchLambda

```python
class ChannelSwitchLambda:
    async def handler(self, event: ConnectEvent, context: object) -> JsonDict:
        """音声⇔チャット切り替え時、同一 ContactId をキーに文脈を引き継ぐ。
        戻り値: {"handover_summary": str, "channel_from": str, "channel_to": str}
        例外: SessionNotFoundError"""
```

### SessionContextManager

```python
class SessionContextManager:
    async def get(self, contact_id: str) -> "SessionContext":
        """セッション文脈を取得。例外: SessionNotFoundError"""

    async def update(self, contact_id: str, turn: ConversationTurn) -> None:
        """ターン追記・要約更新。例外: DynamoAccessError"""

    async def summarize(self, contact_id: str, last_n: int = 5) -> str:
        """直近 N ターンの引き継ぎ要約を生成。"""
```

### EscalationLambda

```python
class EscalationLambda:
    async def handler(self, event: ConnectEvent, context: object) -> JsonDict:
        """ナレッジ未ヒット時、有人キューへの転送属性を返す。
        戻り値: {"escalate": bool, "queue_arn": str | None, "reason": str}
        例外: ConfigError（キュー未設定）"""
```

---

## 4. U-05: SDK & Customer Profile

### CustomerProfileLambda

```python
class CustomerProfileLambda:
    async def handler(self, event: ConnectEvent, context: object) -> JsonDict:
        """au ID ハッシュから顧客属性を解決し Connect 属性へ付与。
        戻り値: {"customer_id": str, "tier": str | None, "found": bool}
        例外: ProfileLookupError"""
```

### CrmWriterLambda

```python
class CrmWriterLambda:
    async def handler(self, event: JsonDict, context: object) -> JsonDict:
        """コンタクト終了時に会話サマリーを CRM API へ書き込み（Secrets Manager で認証）。
        戻り値: {"written": bool, "crm_record_id": str | None}
        例外: SecretsError, CrmApiError（リトライ後 DLQ）"""

    async def write_summary(self, customer_id: str, summary: str) -> str:
        """CRM へ書き込み、CRM レコード ID を返す。"""
```

### IdentityHasher

```python
class IdentityHasher:
    def hash_au_id(self, au_id: str) -> str:
        """au ID の SHA-256 ハッシュ（hex）を返す。平文を保持・ログ出力しない（同期）。
        例外: ValidationError（空入力）"""
```

---

## 5. U-06: Self-Improvement Pipeline

### ContactLensAnalyzerLambda

```python
class ContactLensAnalyzerLambda:
    async def handler(self, event: JsonDict, context: object) -> JsonDict:
        """週次。Contact Lens 出力から低品質コンタクト（CSAT ≤ 2 または エスカレーション）を抽出し
        ContactAnalysis テーブルへ保存。
        戻り値: {"analyzed": int, "low_quality": int}
        例外: ContactLensError, DynamoAccessError"""

    async def extract_low_quality(self, week_start: str) -> list[JsonDict]:
        """対象週の低品質コンタクトを抽出。"""
```

### GapAnalyzerLambda

```python
class GapAnalyzerLambda:
    async def handler(self, event: JsonDict, context: object) -> JsonDict:
        """ContactAnalysis アイテム群を Claude でギャップ分析し、不足/不明瞭カテゴリを分類・スコアリング。
        戻り値: {"gaps": list[dict], "count": int}
        例外: BedrockError, ResponseParseError"""

    async def analyze(self, contacts: list[JsonDict]) -> list[JsonDict]:
        """ギャップ分類結果（category, confusion_score, evidence_contact_ids）を返す。"""
```

### SuggestionGeneratorLambda

```python
class SuggestionGeneratorLambda:
    async def handler(self, event: JsonDict, context: object) -> JsonDict:
        """ギャップを「わかりにくさスコア」で順位付けし上位 10 件の改善提案を生成・保存。
        戻り値: {"generated": int}  # 0 <= generated <= 10
        例外: BedrockError, DynamoAccessError"""

    async def generate(self, gaps: list[JsonDict], top_n: int = 10) -> list[JsonDict]:
        """改善提案（target_url, suggestion, priority_score, status="pending"）を生成。"""
```

---

## 6. U-07: Admin Dashboard

### DashboardApiLambda

```python
class DashboardApiLambda:
    async def handler(self, event: JsonDict, context: object) -> JsonDict:
        """API Gateway（Cognito 認証）背後のルータ。GET /suggestions, PATCH /suggestions/{id},
        GET /metrics をディスパッチ。
        戻り値: {"statusCode": int, "body": str}  # JSON 文字列
        例外: UnauthorizedError, ValidationError, NotFoundError"""

    async def list_suggestions(self, status: str | None = None) -> list[JsonDict]:
        """改善提案一覧を取得（status で絞り込み可）。"""

    async def update_suggestion_status(self, suggestion_id: str, status: str) -> JsonDict:
        """提案ステータスを approved/rejected/held に更新。例外: NotFoundError, ValidationError"""
```

### MetricsAggregatorLambda

```python
class MetricsAggregatorLambda:
    async def handler(self, event: JsonDict, context: object) -> JsonDict:
        """週次コンタクト件数・チャネル別割合・エスカレーション率・平均ターン数を集計。
        戻り値: {"period": str, "contacts": int, "channel_ratio": dict[str, float],
                 "escalation_rate": float, "avg_turns": float}
        例外: DynamoAccessError"""
```

### ApiClient（TypeScript / 参考）

```typescript
class ApiClient {
  async listSuggestions(status?: string): Promise<Suggestion[]>;
  async updateSuggestionStatus(id: string, status: SuggestionStatus): Promise<Suggestion>;
  async getMetrics(period?: string): Promise<Metrics>;
  // Cognito トークンを Authorization ヘッダへ自動付与。401 時は再認証へ誘導。
}
```

---

## 7. 共通例外型

```python
class AppError(Exception):
    """全アプリ例外の基底。code / message を持つ。"""

# インフラ／外部サービス
class DynamoAccessError(AppError): ...
class S3AccessError(AppError): ...
class ObjectNotFoundError(AppError): ...
class BedrockError(AppError): ...
class BedrockThrottledError(BedrockError): ...        # リトライ対象（指数バックオフ）
class EmbeddingError(BedrockError): ...
class ComprehendError(AppError): ...
class ContactLensError(AppError): ...
class SecretsError(AppError): ...
class CrmApiError(AppError): ...                       # リトライ後 DLQ

# クロール／パイプライン
class CrawlerError(AppError): ...
class RobotsDisallowedError(CrawlerError): ...
class RobotsNotLoadedError(CrawlerError): ...
class FetchTimeoutError(CrawlerError): ...
class ParseError(CrawlerError): ...

# ドメイン／検索
class SearchError(AppError): ...
class ResponseParseError(AppError): ...
class SessionNotFoundError(AppError): ...
class ProfileLookupError(AppError): ...
class ConfigError(AppError): ...

# API／検証
class ValidationError(AppError): ...
class UnauthorizedError(AppError): ...
class NotFoundError(AppError): ...
class TimeoutBudgetExceeded(AppError): ...             # 8 秒制約超過時フォールバック
```

> 全 Lambda ハンドラは `AppError` を捕捉し構造化 JSON ログ（PII 除外）を出力したうえで、Connect 経由のものはフォールバック応答、非同期パイプラインのものは DLQ 送出を行う。
