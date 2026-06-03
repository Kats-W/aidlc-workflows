import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiClient, Suggestion } from '../api/ApiClient';
import SuggestionStatusControl from '../components/SuggestionStatusControl';

/** Return the ISO-week label for a Date (e.g. "2026-W23"). */
function isoWeekLabel(date: Date): string {
  // Copy and shift to Thursday of the current week (ISO-8601 rule).
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

/** Build the current week plus the previous 11 weeks (max 12 weeks). */
function recentWeeks(count = 12): string[] {
  const labels: string[] = [];
  const now = new Date();
  for (let i = 0; i < count; i += 1) {
    const d = new Date(now);
    d.setDate(now.getDate() - i * 7);
    labels.push(isoWeekLabel(d));
  }
  return labels;
}

export function SuggestionListView() {
  const weeks = useMemo(() => recentWeeks(12), []);
  const [week, setWeek] = useState(weeks[0]);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Suggestion[]>([]);
  const [totalPages, setTotalPages] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiClient.getSuggestions(week, page);
      // priorityScore descending (server already sorts; guard client-side too).
      setData([...res.suggestions].sort((a, b) => b.priorityScore - a.priorityScore));
      setTotalPages(res.totalPages);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [week, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const updateStatus = async (
    id: string,
    status: 'approved' | 'rejected' | 'hold',
    rejectReason?: string,
  ) => {
    try {
      await apiClient.patchSuggestion(id, status, rejectReason);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const exportCsv = async () => {
    try {
      const csv = await apiClient.getSuggestionsCsv(week);
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `suggestions-${week}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section data-testid="suggestion-list" className="suggestion-list">
      <div className="toolbar">
        <select
          data-testid="week-selector"
          value={week}
          onChange={(e) => {
            setWeek(e.target.value);
            setPage(1);
          }}
        >
          {weeks.map((w) => (
            <option key={w} value={w}>
              {w}
            </option>
          ))}
        </select>
        <button type="button" data-testid="csv-export-button" onClick={() => void exportCsv()}>
          CSV エクスポート
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && <p>読み込み中...</p>}

      <ul>
        {data.map((s) => (
          <li key={s.suggestionId} data-testid={`suggestion-item-${s.suggestionId}`}>
            <div className="suggestion-body">
              <a href={s.targetUrl} target="_blank" rel="noreferrer">
                {s.targetUrl}
              </a>
              <p>{s.improvementText}</p>
              <span className="priority">優先度: {s.priorityScore}</span>
            </div>
            <SuggestionStatusControl
              suggestionId={s.suggestionId}
              currentStatus={s.status}
              disabled={loading}
              onChange={(status, reason) => void updateStatus(s.suggestionId, status, reason)}
            />
          </li>
        ))}
      </ul>

      <div className="pagination">
        <button
          type="button"
          data-testid="pagination-prev"
          disabled={page <= 1 || loading}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
        >
          前へ
        </button>
        <span>
          {page} / {Math.max(totalPages, 1)}
        </span>
        <button
          type="button"
          data-testid="pagination-next"
          disabled={page >= totalPages || loading}
          onClick={() => setPage((p) => p + 1)}
        >
          次へ
        </button>
      </div>
    </section>
  );
}

export default SuggestionListView;
