/**
 * API client for Sentinel backend.
 *
 * In dev, Vite proxies /api/* to localhost:8000.
 * In production, FastAPI serves the built files directly.
 */

// In dev, Vite proxies /api/* to localhost:8000.
// In production, FastAPI serves everything on the same origin — no prefix needed.
const BASE = import.meta.env.DEV ? '/api' : '';

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

export interface RiskEvent {
  id: number;
  event_type: string;
  severity: string;
  details: Record<string, unknown> | null;
  portfolio_value: number;
  action_taken: string;
  created_at: string;
}

export interface RiskEventsData {
  events: RiskEvent[];
  count: number;
}

export interface PerformanceRecord {
  date: string;
  trades_count: number;
  win_rate: number;
  total_pnl: number;
  sharpe_ratio: number | null;
  max_drawdown: number;
  risk_budget_used: number;
}

export interface PerformanceData {
  strategy_id: string;
  records: PerformanceRecord[];
  count: number;
}

export interface ComponentHealthData {
  status: string;
  consecutive_failures: number;
  total_failures: number;
  last_error: string | null;
  last_check: string | null;
  backoff_seconds: number;
}

export interface SystemHealthData {
  health: {
    overall: string;
    healthy: number;
    total: number;
    components: Record<string, ComponentHealthData>;
    recent_events: Array<{
      time: string;
      component: string;
      type: string;
      details: string;
    }>;
  };
  scheduler: SchedulerStatus;
  risk_engine: { circuit_breaker_active: boolean };
}

export interface LearningData {
  learning: {
    enabled: boolean;
    fast_loop: string;
    slow_loop: string;
  };
  scheduler_running: boolean;
}

// --- API calls ---

export const fetchHealth = () => get<HealthData>('/health');
export const fetchPortfolio = () => get<PortfolioData>('/portfolio');
export const fetchTrades = (limit = 20) => get<TradesData>(`/trades?limit=${limit}`);
export const fetchStrategies = () => get<StrategiesData>('/strategies');
export const fetchRiskEvents = (limit = 10) => get<RiskEventsData>(`/risk-events?limit=${limit}`);
export const fetchSystemHealth = () => get<SystemHealthData>('/system-health');
export const fetchLearning = () => get<LearningData>('/learning');
export const emergencyStop = () => post<unknown>('/emergency-stop');
export const pauseAsset = (ac: string) => post<unknown>(`/scheduler/pause/${ac}`);
export const resumeAsset = (ac: string) => post<unknown>(`/scheduler/resume/${ac}`);
