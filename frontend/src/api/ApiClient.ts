import axios, { AxiosError, AxiosInstance, AxiosRequestConfig } from 'axios';
import { fetchAuthSession, signOut } from 'aws-amplify/auth';

/** A single improvement suggestion as returned by the dashboard API. */
export interface Suggestion {
  suggestionId: string;
  status: 'pending' | 'approved' | 'rejected' | 'hold';
  weekStart: string;
  targetUrl: string;
  improvementText: string;
  priorityScore: number;
  createdAt: string;
  updatedAt?: string;
  rejectReason?: string;
}

/** Paginated suggestion-list response. */
export interface SuggestionListResponse {
  suggestions: Suggestion[];
  total: number;
  page: number;
  totalPages: number;
}

/** Usage-statistics metrics response. */
export interface MetricsResponse {
  period: string;
  contacts: { total: number; voice: number; chat: number };
  escalationRate: number;
  avgCsat: number | null;
  avgTurns: number;
  aiResolutionRate: number;
}

/** PATCH response for a status change. */
export interface PatchSuggestionResponse {
  suggestionId: string;
  status: string;
  updatedAt: string;
}

/**
 * Thin client for the dashboard HTTP API.
 *
 * The Cognito ID token is attached to every request as a Bearer token. On a
 * 401 the session is force-refreshed once and the request retried; if that
 * still fails the user is signed out (re-login prompt).
 */
export class ApiClient {
  private readonly http: AxiosInstance;

  constructor(baseUrl: string = import.meta.env.VITE_API_ENDPOINT ?? '') {
    this.http = axios.create({ baseURL: baseUrl });
  }

  private async authHeader(forceRefresh = false): Promise<string> {
    const session = await fetchAuthSession({ forceRefresh });
    const token = session.tokens?.idToken?.toString();
    if (!token) {
      throw new Error('No Cognito ID token available');
    }
    return `Bearer ${token}`;
  }

  private async request<T>(config: AxiosRequestConfig): Promise<T> {
    try {
      const headers = { ...config.headers, Authorization: await this.authHeader() };
      const res = await this.http.request<T>({ ...config, headers });
      return res.data;
    } catch (err) {
      const status = (err as AxiosError).response?.status;
      if (status === 401) {
        // Token may be expired: force-refresh and retry exactly once.
        try {
          const headers = {
            ...config.headers,
            Authorization: await this.authHeader(true),
          };
          const res = await this.http.request<T>({ ...config, headers });
          return res.data;
        } catch {
          await signOut();
          throw new Error('Session expired. Please sign in again.');
        }
      }
      throw err;
    }
  }

  async getSuggestions(week?: string, page = 1, limit = 10): Promise<SuggestionListResponse> {
    return this.request<SuggestionListResponse>({
      method: 'GET',
      url: '/suggestions',
      params: { week, page, limit },
    });
  }

  async patchSuggestion(
    id: string,
    status: string,
    rejectReason?: string,
  ): Promise<PatchSuggestionResponse> {
    return this.request<PatchSuggestionResponse>({
      method: 'PATCH',
      url: `/suggestions/${encodeURIComponent(id)}`,
      data: { status, rejectReason },
    });
  }

  async getMetrics(period: '7d' | '30d'): Promise<MetricsResponse> {
    return this.request<MetricsResponse>({
      method: 'GET',
      url: '/metrics',
      params: { period },
    });
  }

  async getSuggestionsCsv(week?: string): Promise<string> {
    return this.request<string>({
      method: 'GET',
      url: '/suggestions/csv',
      params: { week },
      responseType: 'text',
    });
  }
}

export const apiClient = new ApiClient();
