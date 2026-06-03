import { useCallback, useEffect, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { apiClient, MetricsResponse } from '../api/ApiClient';

export function MetricsView() {
  const [period, setPeriod] = useState<'7d' | '30d'>('7d');
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setMetrics(await apiClient.getMetrics(period));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    void load();
  }, [load]);

  const contactsData = metrics
    ? [
        { name: '音声', value: metrics.contacts.voice },
        { name: 'チャット', value: metrics.contacts.chat },
      ]
    : [];

  const escalationData = metrics
    ? [
        { name: 'エスカレーション率', value: Number((metrics.escalationRate * 100).toFixed(1)) },
        { name: 'AI解決率', value: Number((metrics.aiResolutionRate * 100).toFixed(1)) },
      ]
    : [];

  return (
    <section data-testid="metrics-view" className="metrics-view">
      <div className="toolbar">
        <button
          type="button"
          data-testid="period-selector-7d"
          className={period === '7d' ? 'active' : ''}
          onClick={() => setPeriod('7d')}
        >
          7日
        </button>
        <button
          type="button"
          data-testid="period-selector-30d"
          className={period === '30d' ? 'active' : ''}
          onClick={() => setPeriod('30d')}
        >
          30日
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && <p>読み込み中...</p>}

      {metrics && (
        <div className="summary">
          <span>総コンタクト: {metrics.contacts.total}</span>
          <span>平均CSAT: {metrics.avgCsat ?? '—'}</span>
          <span>平均ターン: {metrics.avgTurns}</span>
        </div>
      )}

      <div className="chart" data-testid="chart-contacts">
        <h3>チャネル別コンタクト数</h3>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={contactsData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis allowDecimals={false} />
            <Tooltip />
            <Bar dataKey="value" fill="#0066cc" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="chart" data-testid="chart-escalation">
        <h3>エスカレーション率 / AI解決率 (%)</h3>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={escalationData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis domain={[0, 100]} />
            <Tooltip />
            <Line type="monotone" dataKey="value" stroke="#cc3300" />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

export default MetricsView;
