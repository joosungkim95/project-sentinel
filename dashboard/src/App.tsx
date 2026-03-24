import { useCallback, useState } from 'react';
import {
  fetchHealth,
  fetchPortfolio,
  fetchTrades,
  fetchStrategies,
  fetchRiskEvents,
  fetchSystemHealth,
  fetchLearning,
  emergencyStop,
  pauseAsset,
  resumeAsset,
  type HealthData,
  type PortfolioData,
  type TradesData,
  type StrategiesData,
  type SchedulerJob,
  type RiskEventsData,
  type SystemHealthData,
  type LearningData,
} from './api';
import { usePolling } from './hooks';

// --- Small helper components ---

function Badge({ children, color }: { children: React.ReactNode; color: string }) {
  const colors: Record<string, string> = {
    green: 'bg-emerald-900/50 text-emerald-400 border-emerald-800',
    red: 'bg-red-900/50 text-red-400 border-red-800',
    yellow: 'bg-amber-900/50 text-amber-400 border-amber-800',
    blue: 'bg-blue-900/50 text-blue-400 border-blue-800',
    zinc: 'bg-zinc-800 text-zinc-400 border-zinc-700',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded border font-mono ${colors[color] ?? colors.zinc}`}>
      {children}
    </span>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg">
      <div className="px-4 py-3 border-b border-zinc-800">
        <h2 className="text-sm font-medium text-zinc-300">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="text-xs text-zinc-500 mb-1">{label}</div>
      <div className="text-lg font-mono text-zinc-100">{value}</div>
      {sub && <div className="text-xs text-zinc-500 mt-0.5">{sub}</div>}
    </div>
  );
}

// --- Panels ---

function PortfolioPanel({ data }: { data: PortfolioData | null }) {
  if (!data || data.status === 'no_data') {
    return (
      <Card title="Portfolio">
        <p className="text-zinc-500 text-sm">No portfolio data yet.</p>
      </Card>
    );
  }
  const positions = data.positions ? Object.keys(data.positions).length : 0;
  return (
    <Card title="Portfolio">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Stat label="Total Value" value={`$${(data.total_value ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}`} />
        <Stat label="Cash" value={`$${(data.cash ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}`} />
        <Stat label="Positions" value={String(positions)} />
        <Stat
          label="Risk Util."
          value={
            data.risk_utilization
              ? Object.values(data.risk_utilization).map((v) => `${v.toFixed(1)}%`).join(' / ') || '0%'
              : '0%'
          }
          sub={data.risk_utilization ? Object.keys(data.risk_utilization).join(' / ') : ''}
        />
      </div>
    </Card>
  );
}

function SchedulerPanel({
  health,
  onPause,
  onResume,
}: {
  health: HealthData | null;
  onPause: (ac: string) => void;
  onResume: (ac: string) => void;
}) {
  if (!health) return null;
  const s = health.scheduler;
  return (
    <Card title="Scheduler">
      <div className="flex items-center gap-3 mb-4">
        <Badge color={s.running ? 'green' : 'red'}>{s.running ? 'RUNNING' : 'STOPPED'}</Badge>
        <Badge color={s.market_open ? 'green' : 'zinc'}>{s.market_open ? 'MARKET OPEN' : 'MARKET CLOSED'}</Badge>
        <span className="text-xs text-zinc-500 font-mono ml-auto">
          {health.strategies} strategies
        </span>
      </div>
      <div className="space-y-2">
        {Object.entries(s.jobs).map(([ac, job]: [string, SchedulerJob]) => (
          <div key={ac} className="flex items-center justify-between bg-zinc-950 rounded px-3 py-2 border border-zinc-800">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm text-zinc-300 w-24">{ac}</span>
              <Badge color={job.paused ? 'red' : job.strategies > 0 ? 'green' : 'zinc'}>
                {job.paused ? 'PAUSED' : job.strategies > 0 ? 'ACTIVE' : 'EMPTY'}
              </Badge>
              {job.consecutive_errors > 0 && (
                <Badge color="yellow">{job.consecutive_errors} errors</Badge>
              )}
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs text-zinc-500 font-mono">
                {job.cycles_completed} cycles
              </span>
              {job.strategies > 0 && (
                <button
                  onClick={() => (job.paused ? onResume(ac) : onPause(ac))}
                  className={`text-xs px-2 py-1 rounded border cursor-pointer ${
                    job.paused
                      ? 'border-emerald-800 text-emerald-400 hover:bg-emerald-900/30'
                      : 'border-zinc-700 text-zinc-400 hover:bg-zinc-800'
                  }`}
                >
                  {job.paused ? 'Resume' : 'Pause'}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function StrategiesPanel({ data }: { data: StrategiesData | null }) {
  if (!data) return null;
  const assetColors: Record<string, string> = {
    equities: 'blue',
    crypto: 'yellow',
    predictions: 'green',
  };
  return (
    <Card title="Strategies">
      {data.strategies.length === 0 ? (
        <p className="text-zinc-500 text-sm">No strategies registered.</p>
      ) : (
        <div className="space-y-2">
          {data.strategies.map((s) => (
            <div key={s.id} className="flex items-center justify-between bg-zinc-950 rounded px-3 py-2 border border-zinc-800">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm text-zinc-200">{s.id}</span>
                <Badge color={assetColors[s.asset_class] ?? 'zinc'}>{s.asset_class}</Badge>
                <Badge color={s.status === 'active' ? 'green' : s.status === 'paper_testing' ? 'yellow' : 'red'}>
                  {s.status}
                </Badge>
              </div>
              <span className="text-xs text-zinc-500 font-mono">
                {s.parameters.symbol ? String(s.parameters.symbol) : 'scan'}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function TradesPanel({ data }: { data: TradesData | null }) {
  if (!data) return null;
  return (
    <Card title="Recent Trades">
      {data.trades.length === 0 ? (
        <p className="text-zinc-500 text-sm">No trades yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-zinc-500 border-b border-zinc-800">
                <th className="text-left py-2 font-medium">Time</th>
                <th className="text-left py-2 font-medium">Strategy</th>
                <th className="text-left py-2 font-medium">Symbol</th>
                <th className="text-left py-2 font-medium">Side</th>
                <th className="text-right py-2 font-medium">Qty</th>
                <th className="text-right py-2 font-medium">Price</th>
                <th className="text-right py-2 font-medium">P&L</th>
              </tr>
            </thead>
            <tbody>
              {data.trades.map((t) => (
                <tr key={t.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                  <td className="py-2 font-mono text-xs text-zinc-500">
                    {t.created_at ? new Date(t.created_at).toLocaleTimeString() : '--'}
                  </td>
                  <td className="py-2 text-zinc-300">{t.strategy_id}</td>
                  <td className="py-2 font-mono text-zinc-200">{t.symbol}</td>
                  <td className="py-2">
                    <Badge color={t.side === 'buy' ? 'green' : 'red'}>{t.side.toUpperCase()}</Badge>
                  </td>
                  <td className="py-2 text-right font-mono text-zinc-300">{t.quantity}</td>
                  <td className="py-2 text-right font-mono text-zinc-300">
                    ${t.price?.toLocaleString('en-US', { minimumFractionDigits: 2 }) ?? '--'}
                  </td>
                  <td className={`py-2 text-right font-mono ${
                    t.pnl === null ? 'text-zinc-500' : t.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                  }`}>
                    {t.pnl !== null ? `$${t.pnl.toFixed(2)}` : '--'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function ConnectionsPanel({ health }: { health: HealthData | null }) {
  if (!health) return null;
  return (
    <Card title="System">
      <div className="grid grid-cols-2 gap-3">
        {Object.entries(health.connections).map(([name, status]) => (
          <div key={name} className="flex items-center justify-between">
            <span className="text-sm text-zinc-400">{name}</span>
            <Badge color={status === 'connected' ? 'green' : status === 'pending' ? 'yellow' : 'red'}>
              {status}
            </Badge>
          </div>
        ))}
        <div className="flex items-center justify-between">
          <span className="text-sm text-zinc-400">uptime</span>
          <span className="text-xs font-mono text-zinc-500">
            {health.started_at ? `since ${new Date(health.started_at).toLocaleTimeString()}` : '--'}
          </span>
        </div>
      </div>
    </Card>
  );
}

function RiskEventsPanel({ data }: { data: RiskEventsData | null }) {
  if (!data || data.events.length === 0) return null;
  const severityColor: Record<string, string> = {
    info: 'zinc',
    warning: 'yellow',
    critical: 'red',
  };
  return (
    <Card title="Risk Events">
      <div className="space-y-2 max-h-48 overflow-y-auto">
        {data.events.map((e) => (
          <div key={e.id} className="flex items-center justify-between bg-zinc-950 rounded px-3 py-2 border border-zinc-800">
            <div className="flex items-center gap-2">
              <Badge color={severityColor[e.severity] ?? 'zinc'}>{e.severity}</Badge>
              <span className="text-sm text-zinc-300">{e.event_type}</span>
              <span className="text-xs text-zinc-500">{e.action_taken}</span>
            </div>
            <span className="text-xs text-zinc-500 font-mono">
              {e.created_at ? new Date(e.created_at).toLocaleTimeString() : '--'}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function SystemHealthPanel({ data }: { data: SystemHealthData | null }) {
  if (!data) return null;
  const h = data.health;
  const overallColor = h.overall === 'healthy' ? 'green' : h.overall === 'degraded' ? 'yellow' : 'red';
  return (
    <Card title="System Health">
      <div className="flex items-center gap-3 mb-4">
        <Badge color={overallColor}>{h.overall.toUpperCase()}</Badge>
        <span className="text-xs text-zinc-500 font-mono">{h.healthy}/{h.total} components healthy</span>
        {data.risk_engine.circuit_breaker_active && (
          <Badge color="red">CIRCUIT BREAKER</Badge>
        )}
      </div>
      <div className="space-y-1">
        {Object.entries(h.components).map(([name, comp]) => (
          <div key={name} className="flex items-center justify-between text-sm">
            <span className="text-zinc-400">{name}</span>
            <div className="flex items-center gap-2">
              <Badge color={comp.status === 'healthy' ? 'green' : comp.status === 'degraded' ? 'yellow' : 'red'}>
                {comp.status}
              </Badge>
              {comp.total_failures > 0 && (
                <span className="text-xs text-zinc-500 font-mono">{comp.total_failures} failures</span>
              )}
            </div>
          </div>
        ))}
      </div>
      {h.recent_events.length > 0 && (
        <div className="mt-3 pt-3 border-t border-zinc-800">
          <div className="text-xs text-zinc-500 mb-2">Recent Events</div>
          {h.recent_events.slice(0, 3).map((ev, i) => (
            <div key={i} className="text-xs text-zinc-500 font-mono truncate">
              {new Date(ev.time).toLocaleTimeString()} [{ev.type}] {ev.details}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function LearningPanel({ data }: { data: LearningData | null }) {
  if (!data) return null;
  return (
    <Card title="Learning Engine">
      <div className="flex items-center gap-3 mb-3">
        <Badge color={data.learning.enabled ? 'green' : 'zinc'}>
          {data.learning.enabled ? 'ENABLED' : 'DISABLED'}
        </Badge>
        <Badge color={data.scheduler_running ? 'green' : 'red'}>
          {data.scheduler_running ? 'SCHEDULER ON' : 'SCHEDULER OFF'}
        </Badge>
      </div>
      <div className="space-y-1 text-sm">
        <div className="flex justify-between">
          <span className="text-zinc-500">Fast loop (daily)</span>
          <span className="font-mono text-zinc-300">{data.learning.fast_loop}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">Slow loop (weekly)</span>
          <span className="font-mono text-zinc-300">{data.learning.slow_loop}</span>
        </div>
      </div>
    </Card>
  );
}

// --- Main App ---

export default function App() {
  const [stopping, setStopping] = useState(false);

  const healthFetcher = useCallback(() => fetchHealth(), []);
  const portfolioFetcher = useCallback(() => fetchPortfolio(), []);
  const tradesFetcher = useCallback(() => fetchTrades(20), []);
  const strategiesFetcher = useCallback(() => fetchStrategies(), []);
  const riskEventsFetcher = useCallback(() => fetchRiskEvents(10), []);
  const systemHealthFetcher = useCallback(() => fetchSystemHealth(), []);
  const learningFetcher = useCallback(() => fetchLearning(), []);

  const health = usePolling(healthFetcher, 5000);
  const portfolio = usePolling(portfolioFetcher, 10000);
  const trades = usePolling(tradesFetcher, 10000);
  const strategies = usePolling(strategiesFetcher, 15000);
  const riskEvents = usePolling(riskEventsFetcher, 15000);
  const systemHealth = usePolling(systemHealthFetcher, 10000);
  const learning = usePolling(learningFetcher, 30000);

  const handleEmergencyStop = async () => {
    if (!confirm('EMERGENCY STOP: This will halt all trading. Continue?')) return;
    setStopping(true);
    try {
      await emergencyStop();
      health.refresh();
    } finally {
      setStopping(false);
    }
  };

  const handlePause = async (ac: string) => {
    await pauseAsset(ac);
    health.refresh();
  };

  const handleResume = async (ac: string) => {
    await resumeAsset(ac);
    health.refresh();
  };

  const isConnected = !health.error;

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {/* Header */}
      <header className="border-b border-zinc-800 px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold tracking-tight">Sentinel</h1>
            <Badge color={isConnected ? 'green' : 'red'}>
              {isConnected ? 'ONLINE' : 'OFFLINE'}
            </Badge>
          </div>
          <button
            onClick={handleEmergencyStop}
            disabled={stopping}
            className="bg-red-900/50 hover:bg-red-900 text-red-300 border border-red-800 px-4 py-1.5 rounded text-sm font-medium cursor-pointer disabled:opacity-50 transition-colors"
          >
            {stopping ? 'Stopping...' : 'Emergency Stop'}
          </button>
        </div>
      </header>

      {/* Connection error banner */}
      {health.error && (
        <div className="bg-red-950 border-b border-red-900 px-6 py-2">
          <p className="max-w-6xl mx-auto text-sm text-red-400 font-mono">
            Backend unreachable: {health.error}
          </p>
        </div>
      )}

      {/* Dashboard grid */}
      <main className="max-w-6xl mx-auto px-6 py-6 space-y-4">
        <PortfolioPanel data={portfolio.data} />

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <SchedulerPanel
            health={health.data}
            onPause={handlePause}
            onResume={handleResume}
          />
          <StrategiesPanel data={strategies.data} />
        </div>

        <TradesPanel data={trades.data} />

        <RiskEventsPanel data={riskEvents.data} />

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <SystemHealthPanel data={systemHealth.data} />
          <LearningPanel data={learning.data} />
        </div>

        <ConnectionsPanel health={health.data} />
      </main>

      {/* Footer */}
      <footer className="border-t border-zinc-800 px-6 py-3 mt-auto">
        <p className="max-w-6xl mx-auto text-xs text-zinc-600 font-mono text-center">
          Sentinel v0.2.0 — polling every 5s
        </p>
      </footer>
    </div>
  );
}
