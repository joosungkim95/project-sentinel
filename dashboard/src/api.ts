/**
 * API client for Sentinel backend.
 *
 * In dev, Vite proxies /api/* to localhost:8000.
 * In production, FastAPI serves the built files directly.
 */

const BASE = '/api';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function post<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'POST' });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// --- Types ---

export interface SchedulerJob {
  strategies: number;
  paused: boolean;
  consecutive_errors: number;
  cycles_completed: number;
  last_run: string | null;
}

export interface SchedulerStatus {
  running: boolean;
  enabled: boolean;
  market_open: boolean;
  jobs: Record<string, SchedulerJob>;
}

export interface HealthData {
  status: string;
  timestamp: string;
  started_at: string | null;
  strategies: number;
  scheduler: SchedulerStatus;
  connections: Record<string, string>;
}

export interface PortfolioData {
  total_value?: number;
  cash?: number;
  positions?: Record<string, unknown>;
  risk_utilization?: Record<string, number>;
  timestamp?: string;
  status?: string;
  message?: string;
}

export interface Trade {
  id: number;
  strategy_id: string;
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  risk_check_result: string;
  pnl: number | null;
  market_regime: string;
  created_at: string;
}

export interface TradesData {
  trades: Trade[];
  count: number;
}

export interface StrategyInfo {
  id: string;
  asset_class: string;
  status: string;
  parameters: Record<string, unknown>;
}

export interface StrategiesData {
  strategies: StrategyInfo[];
}

// --- API calls ---

export const fetchHealth = () => get<HealthData>('/health');
export const fetchPortfolio = () => get<PortfolioData>('/portfolio');
export const fetchTrades = (limit = 20) => get<TradesData>(`/trades?limit=${limit}`);
export const fetchStrategies = () => get<StrategiesData>('/strategies');
export const emergencyStop = () => post<unknown>('/emergency-stop');
export const pauseAsset = (ac: string) => post<unknown>(`/scheduler/pause/${ac}`);
export const resumeAsset = (ac: string) => post<unknown>(`/scheduler/resume/${ac}`);
