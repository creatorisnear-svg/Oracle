import { useState, useEffect, useRef, useCallback } from "react";
import {
  createChart, ColorType, CrosshairMode,
  CandlestickSeries, LineSeries, HistogramSeries,
  createSeriesMarkers,
  type IChartApi, type UTCTimestamp,
} from "lightweight-charts";

const API_BASE = "/api";
const WS_BASE = typeof window !== "undefined"
  ? (window.location.protocol === "https:" ? "wss" : "ws") + "://" + window.location.host
  : "";

// ─── Types ───────────────────────────────────────────────────────────────────
type Signal = "BUY_CALL" | "BUY_PUT" | "HOLD";

interface AgentVote {
  agent: string; emoji: string; vote: Signal;
  confidence: number; reason: string; weight?: number; method?: string;
}
interface TargetHitBreakdownRow {
  indicator: string; direction: "↑" | "↓" | "·";
  weight: number; contrib: number; detail: string;
  supports_target: boolean;
}
interface HorizonInfo {
  key: string; label: string; threshold: number;
  bar_minutes: number; forecast_bars: number; expiry_pref: string;
}
interface Horizon {
  key: string; label: string; interval: string; period: string;
  forecast_bars: number; bar_minutes: number; threshold: number; expiry_pref: string;
}
interface Judgment {
  signal: Signal; confidence: number;
  horizon?: HorizonInfo;
  target_hit_prob?: number;
  target_hit_base_pct?: number;
  target_hit_alignment?: number;
  target_hit_boost_pct?: number;
  target_hit_breakdown?: TargetHitBreakdownRow[];
  target_hit_method?: string;
  target_hit_days?: number;
  vote_consensus_pct?: number;
  entry_price: number; stop_loss: number; target_price: number;
  agreed_agents: string[]; disagreed_agents: string[];
  vote_tally: { BUY_CALL: number; BUY_PUT: number; HOLD: number };
  sticky?: { kind: string; open_since?: string; age?: string; from?: string; message: string } | null;
  position_size_pct: number; judge_reason: string;
  evidence_reason?: string | null;
  evidence_pillars?: {
    trend: boolean; momentum: boolean; volume: boolean; price: boolean;
    aligned: number; total: number; score?: number;
  } | null;
  macro_context?: {
    adx: number;
    weekly_trend: { dir: "up" | "down" | "flat" | "self"; strength: number; ema20?: number };
    spy_trend: { dir: "up" | "down" | "flat" | "self"; pct_from_ema50: number; change_1d?: number };
    regime?: { label: string; vix: number; spy_above_200ema?: boolean; golden_cross?: boolean };
  } | null;
  action: string; strike_hint: string; expiry_hint: string;
  expiry_weekly: string; expiry_biweekly: string; expiry_monthly: string;
  entry_trigger: string; risk_note: string;
  forecast_line?: { time: number; value: number }[];
  post_forecast_line?: { time: number; value: number }[];
  post_forecast_mode?: "continuation" | "reversion" | "drift" | null;
  post_forecast_note?: string;
  post_forecast_score?: number;
  fear_greed_score?: number; fear_greed_label?: string;
  kelly?: {
    kelly_pct: number; dollars_per_10k: number; regime: string;
    win_prob_used: number; rr_planned: number; kelly_raw: number;
    explanation: string;
  };
  track_record?: {
    hit_rate: number; signals: number;
    rating: "strong" | "good" | "weak" | "poor";
    note: string;
  } | null;
}
interface LivePrice {
  symbol: string; price: number; change_pct: number;
  ts: number; market_state: string;
  news?: NewsItem[];
}
interface NewsItem {
  title: string; summary?: string; source: string;
  url: string; published_at: string; category?: string;
}
interface OptionRow {
  strike: number; last: number; bid: number; ask: number; mid: number;
  volume: number; open_interest: number; iv: number; itm: boolean; delta: number;
}
interface OptionsChain {
  symbol: string; price: number; selected_expiry: string;
  available_expiries: string[]; calls: OptionRow[]; puts: OptionRow[];
}
interface FearGreedComp { name: string; score: number; value: number; label: string; weight: number; }
interface FearGreed { score: number; label: string; color: string; components: FearGreedComp[]; }
interface StockSentiment { symbol: string; score: number; label: string; color: string; components: FearGreedComp[]; }
interface SearchResult { symbol: string; name: string; exchange: string; type: string; }
interface AccuracyAgent {
  agent: string; total: number; correct: number;
  win_rate: number; call_correct: number; put_correct: number;
  call_total: number; put_total: number; weight: number;
}
interface AccuracyReport {
  overall_win_rate?: number; total_predictions?: number;
  correct_predictions?: number; agents?: AccuracyAgent[];
}
interface LearningEvent {
  id: number;
  agent: string;
  symbol: string;
  vote: string;
  system_signal: string;
  was_correct: number;
  created_at: string;
  weight_before: number;
  weight_after: number;
  delta: number;
  phase: "warmup" | "active";
  phase_before: "warmup" | "active";
  total_after: number;
  correct_after: number;
}
interface LearningStatus {
  status?: string;
  db_path?: string;
  db_exists?: boolean;
  db_size_bytes?: number;
  db_modified_iso?: string | null;
  predictions_total?: number;
  predictions_resolved?: number;
  predictions_pending?: number;
  first_prediction_iso?: string | null;
  last_prediction_iso?: string | null;
  last_resolved_iso?: string | null;
  agents?: { name: string; weight: number; total: number; correct: number; accuracy_pct: number | null }[];
  backup_dir?: string;
  backup_count?: number;
  latest_backup?: string | null;
  latest_backup_iso?: string | null;
  latest_backup_size_bytes?: number;
  is_learning?: boolean;
  weights_adjusting?: boolean;
}

// ─── Signal Styling ─────────────────────────────────────────────────────────
function signalStyle(v: Signal) {
  if (v === "BUY_CALL") return { bg: "bg-emerald-500/20", border: "border-emerald-500", text: "text-emerald-400", glow: "shadow-emerald-500/40", hex: "#10b981" };
  if (v === "BUY_PUT")  return { bg: "bg-red-500/20",     border: "border-red-500",     text: "text-red-400",     glow: "shadow-red-500/40",     hex: "#ef4444" };
  return { bg: "bg-slate-500/20", border: "border-slate-500", text: "text-slate-400", glow: "", hex: "#94a3b8" };
}
function SignalBadge({ signal, size = "md" }: { signal: Signal; size?: "sm" | "md" | "lg" }) {
  const st = signalStyle(signal);
  const sz = size === "lg" ? "text-2xl px-8 py-3 font-black tracking-widest" : size === "md" ? "text-sm px-3 py-1 font-bold" : "text-xs px-2 py-0.5 font-semibold";
  const label = signal === "BUY_CALL" ? "⬆ CALL" : signal === "BUY_PUT" ? "⬇ PUT" : "⏸ HOLD";
  return <span className={`rounded-full border ${st.bg} ${st.border} ${st.text} ${sz} shadow-lg ${st.glow}`}>{label}</span>;
}

// ─── Target-Hit Probability Ring ─────────────────────────────────────────
function ConfRing({ pct, signal, label }: { pct: number; signal: Signal; label?: string }) {
  const r = 38; const circ = 2 * Math.PI * r;
  const st = signalStyle(signal);
  return (
    <div className="flex flex-col items-center">
      <svg width="96" height="96" viewBox="0 0 96 96">
        <circle cx="48" cy="48" r={r} stroke="#1e293b" strokeWidth="7" fill="none" />
        <circle cx="48" cy="48" r={r} stroke={st.hex} strokeWidth="7" fill="none"
          strokeDasharray={circ} strokeDashoffset={circ * (1 - pct / 100)}
          strokeLinecap="round" transform="rotate(-90 48 48)" />
        <text x="48" y="53" textAnchor="middle" fill={st.hex} fontSize="17" fontWeight="bold">{pct.toFixed(0)}%</text>
      </svg>
      {label ? <div className="text-[10px] uppercase tracking-wider text-slate-400 mt-1">{label}</div> : null}
    </div>
  );
}

// ─── Target-Hit Probability Breakdown Panel ──────────────────────────────
function TargetHitBreakdown({ judgment }: { judgment: Judgment }) {
  if (judgment.signal === "HOLD" || !judgment.target_hit_breakdown?.length) return null;
  const breakdown = judgment.target_hit_breakdown;
  const base = judgment.target_hit_base_pct ?? 0;
  const boost = judgment.target_hit_boost_pct ?? 0;
  const align = judgment.target_hit_alignment ?? 0;
  const days = judgment.target_hit_days ?? 7;
  const supporting = breakdown.filter(r => r.contrib > 0).length;
  const opposing = breakdown.filter(r => r.contrib < 0).length;
  const neutral = breakdown.filter(r => r.contrib === 0).length;

  return (
    <div className="bg-slate-800/50 rounded-lg p-2.5 space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-xs text-slate-500 font-semibold">% TO TARGET — BREAKDOWN</div>
        <div className="text-[10px] text-slate-500">{days}-day window</div>
      </div>

      <div className="grid grid-cols-3 gap-1 text-center text-[10px]">
        <div className="bg-slate-900/50 rounded p-1">
          <div className="text-slate-500">Vol Base</div>
          <div className="font-mono font-bold text-slate-300">{base.toFixed(0)}%</div>
        </div>
        <div className="bg-slate-900/50 rounded p-1">
          <div className="text-slate-500">Indicator Boost</div>
          <div className={`font-mono font-bold ${boost > 0 ? "text-emerald-400" : boost < 0 ? "text-red-400" : "text-slate-300"}`}>
            {boost > 0 ? "+" : ""}{boost.toFixed(0)}%
          </div>
        </div>
        <div className="bg-slate-900/50 rounded p-1">
          <div className="text-slate-500">Alignment</div>
          <div className={`font-mono font-bold ${align > 0.1 ? "text-emerald-400" : align < -0.1 ? "text-red-400" : "text-slate-300"}`}>
            {(align * 100).toFixed(0)}
          </div>
        </div>
      </div>

      <div className="text-[10px] text-slate-500 text-center">
        <span className="text-emerald-400 font-semibold">{supporting} support</span>
        {" · "}
        <span className="text-red-400 font-semibold">{opposing} oppose</span>
        {" · "}
        <span className="text-slate-400">{neutral} neutral</span>
      </div>

      <div className="space-y-1 max-h-56 overflow-y-auto pr-1">
        {breakdown.map((row, i) => {
          const color = row.contrib > 0 ? "text-emerald-400" : row.contrib < 0 ? "text-red-400" : "text-slate-500";
          const bg = row.contrib > 0 ? "bg-emerald-500/5" : row.contrib < 0 ? "bg-red-500/5" : "bg-slate-700/20";
          return (
            <div key={i} className={`flex items-center justify-between gap-2 rounded px-2 py-1 text-xs ${bg}`}>
              <div className="flex items-center gap-1.5 min-w-0 flex-1">
                <span className={`w-3 text-center ${color} font-bold`}>{row.direction}</span>
                <span className="text-slate-300 font-medium truncate">{row.indicator}</span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-[10px] text-slate-500 font-mono truncate max-w-[110px]" title={row.detail}>{row.detail}</span>
                <span className={`font-mono font-bold ${color} w-10 text-right`}>{row.contrib > 0 ? "+" : ""}{row.contrib.toFixed(1)}</span>
              </div>
            </div>
          );
        })}
      </div>

      {judgment.target_hit_method ? (
        <div className="text-[10px] text-slate-500 italic pt-1 border-t border-slate-700/50">
          {judgment.target_hit_method}
        </div>
      ) : null}
      {typeof judgment.vote_consensus_pct === "number" ? (
        <div className="text-[10px] text-slate-500 text-center">
          Agent vote consensus: <span className="text-slate-300 font-mono">{judgment.vote_consensus_pct.toFixed(0)}%</span>
        </div>
      ) : null}
    </div>
  );
}

// ─── Fear & Greed Gauge ───────────────────────────────────────────────────
function FearGreedGauge({ data }: { data: FearGreed | null }) {
  if (!data) return (
    <div className="flex items-center justify-center h-28 text-slate-500 text-sm">
      Loading Fear & Greed...
    </div>
  );
  const score = data.score;
  const angle = -135 + (score / 100) * 270;
  const color = score >= 75 ? "#10b981" : score >= 55 ? "#22c55e" : score >= 45 ? "#f59e0b" : score >= 25 ? "#f97316" : "#ef4444";
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width="140" height="90" viewBox="0 0 140 90">
        {/* Background arc */}
        <path d="M 15 85 A 55 55 0 1 1 125 85" fill="none" stroke="#1e293b" strokeWidth="10" strokeLinecap="round" />
        {/* Color segments */}
        <path d="M 15 85 A 55 55 0 0 1 32 38" fill="none" stroke="#ef4444" strokeWidth="10" strokeLinecap="butt" opacity="0.6" />
        <path d="M 32 38 A 55 55 0 0 1 55 16" fill="none" stroke="#f97316" strokeWidth="10" strokeLinecap="butt" opacity="0.6" />
        <path d="M 55 16 A 55 55 0 0 1 85 16" fill="none" stroke="#f59e0b" strokeWidth="10" strokeLinecap="butt" opacity="0.6" />
        <path d="M 85 16 A 55 55 0 0 1 108 38" fill="none" stroke="#22c55e" strokeWidth="10" strokeLinecap="butt" opacity="0.6" />
        <path d="M 108 38 A 55 55 0 0 1 125 85" fill="none" stroke="#10b981" strokeWidth="10" strokeLinecap="butt" opacity="0.6" />
        {/* Needle */}
        <g transform={`rotate(${angle}, 70, 85)`}>
          <line x1="70" y1="85" x2="70" y2="35" stroke={color} strokeWidth="3" strokeLinecap="round" />
          <circle cx="70" cy="85" r="5" fill={color} />
        </g>
        <text x="70" y="75" textAnchor="middle" fill={color} fontSize="16" fontWeight="bold">{Math.round(score)}</text>
      </svg>
      <div className="text-sm font-bold" style={{ color }}>{data.label}</div>
      <div className="flex gap-2 text-xs text-slate-500 flex-wrap justify-center">
        {data.components?.slice(0, 3).map(c => (
          <span key={c.name} className="bg-slate-800 px-1.5 py-0.5 rounded">{c.name}: {c.score.toFixed(0)}</span>
        ))}
      </div>
    </div>
  );
}

// ─── Accuracy Bar ─────────────────────────────────────────────────────────
function AccBar({ pct, color = "#10b981" }: { pct: number; color?: string }) {
  return (
    <div className="h-1.5 w-full bg-slate-700 rounded-full overflow-hidden">
      <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

// ─── Stat Pill ──────────────────────────────────────────────────────────
function Stat({ label, value, color = "text-slate-300" }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="bg-slate-800/60 rounded-lg px-2.5 py-1.5 text-center">
      <div className={`text-sm font-bold ${color}`}>{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}

// ─── Main Dashboard ───────────────────────────────────────────────────────
export default function TradingDashboard() {
  const [symbol, setSymbol] = useState("AAPL");
  const [inputSym, setInputSym] = useState("AAPL");
  const [livePrice, setLivePrice] = useState<LivePrice | null>(null);
  const [votes, setVotes] = useState<AgentVote[]>([]);
  const [judgment, setJudgment] = useState<Judgment | null>(null);
  const [status, setStatus] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [tab, setTab] = useState<"signal" | "agents" | "options" | "chain" | "news" | "fear" | "accuracy" | "paper">("signal");
  const [paperAccount, setPaperAccount] = useState<any>(null);
  const [paperOpen, setPaperOpen] = useState<any[]>([]);
  const [paperHistory, setPaperHistory] = useState<any[]>([]);
  const [paperLoading, setPaperLoading] = useState(false);
  const [paperToast, setPaperToast] = useState<string>("");
  const [period, setPeriod] = useState("3mo");
  const [horizon, setHorizon] = useState<string>("swing");
  const [horizons, setHorizons] = useState<Horizon[]>([]);
  const horizonRef = useRef<string>("swing");
  const [indicators, setIndicators] = useState<Record<string, unknown>>({});
  const [politicalNews, setPoliticalNews] = useState<NewsItem[]>([]);
  const [fearGreed, setFearGreed] = useState<FearGreed | null>(null);
  const [stockSentiment, setStockSentiment] = useState<StockSentiment | null>(null);
  const [accuracy, setAccuracy] = useState<AccuracyReport | null>(null);
  const [learning, setLearning] = useState<LearningStatus | null>(null);
  const [learningEvents, setLearningEvents] = useState<LearningEvent[]>([]);
  const [backupBusy, setBackupBusy] = useState(false);
  const [fearLoading, setFearLoading] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchActiveIdx, setSearchActiveIdx] = useState(-1);
  const searchAbortRef = useRef<AbortController | null>(null);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchBoxRef = useRef<HTMLDivElement>(null);
  const [indicators_visible, setIndicatorsVisible] = useState({ ema: true, bb: false, vwap: true, st: false, vol: true });
  const [chain, setChain] = useState<OptionsChain | null>(null);
  const [chainLoading, setChainLoading] = useState(false);
  const [chainExpiry, setChainExpiry] = useState("");
  const [chainSide, setChainSide] = useState<"calls" | "puts">("calls");

  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<any>(null);
  const forecastRef = useRef<any>(null);
  const postForecastRef = useRef<any>(null);
  const targetLineRef = useRef<any>(null);
  const stopLineRef = useRef<any>(null);
  const historyMarkersRef = useRef<any>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const symRef = useRef<string>("");
  const periodRef = useRef<string>("3mo");
  // Live-tick streaming: keep the most recent candle in memory and the bar
  // interval (seconds) so we can mutate its high/low/close as live ticks
  // arrive — and roll into a fresh candle when the bar boundary is crossed.
  const lastCandleRef = useRef<{ time: number; open: number; high: number; low: number; close: number } | null>(null);
  const barIntervalSecRef = useRef<number>(86400);
  // Live-tick volume: the histogram series, the latest bar's value, and the
  // last cumulative day-volume reading. On daily charts we set the bar value
  // = day_volume directly. On intraday we increment by the delta between
  // ticks so each bar shows volume traded inside that bar's window.
  const volSeriesRef = useRef<any>(null);
  const lastVolBarRef = useRef<{ time: number; value: number } | null>(null);
  const prevDayVolRef = useRef<number>(0);
  // Volume-spike alert: 20-bar rolling average + a per-bar "alerted" flag
  // so the banner only flashes once per breakout, not on every tick.
  const avgVol20Ref = useRef<number>(0);
  const spikeAlertedTimeRef = useRef<number>(0);
  const spikeMarkersRef = useRef<any>(null);
  const spikeMarkerListRef = useRef<any[]>([]);
  const [spikeAlert, setSpikeAlert] = useState<{ ratio: number; ts: number } | null>(null);
  const spikeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Chart setup ─────────────────────────────────────────────────────────
  // Tear-down helper. Centralised so EVERY ref that holds a series-or-primitive
  // is cleared before the chart is disposed — otherwise the WebSocket live-tick
  // handler keeps calling .update() on freed objects and lightweight-charts
  // throws "Object is disposed", which then bubbles up through
  // @replit/vite-plugin-runtime-error-modal as the red overlay.
  const disposeChart = useCallback(() => {
    if (chartRef.current) {
      try { chartRef.current.remove(); } catch {}
      chartRef.current = null;
    }
    candleRef.current = null;
    forecastRef.current = null;
    postForecastRef.current = null;
    targetLineRef.current = null;
    stopLineRef.current = null;
    historyMarkersRef.current = null;
    // Live-tick refs (these were the silent leak: still pointing at disposed
    // series after a chart rebuild, so the next live_price tick blew up).
    volSeriesRef.current = null;
    lastCandleRef.current = null;
    lastVolBarRef.current = null;
    prevDayVolRef.current = 0;
    avgVol20Ref.current = 0;
    spikeAlertedTimeRef.current = 0;
    spikeMarkersRef.current = null;
    spikeMarkerListRef.current = [];
  }, []);

  const loadChart = useCallback(async (sym: string, p: string) => {
    if (!chartContainerRef.current) return;
    disposeChart();
    const container = chartContainerRef.current;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight || 350,
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#94a3b8" },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#334155", scaleMargins: { top: 0.1, bottom: 0.2 } },
      timeScale: { borderColor: "#334155", timeVisible: true },
    });
    chartRef.current = chart;

    const fetchPeriod = p === "1d" ? "5d" : p;
    const fetchInterval = p === "1d" ? "5m" : p === "5d" ? "15m" : "1d";
    const url = `${API_BASE}/chart/${sym}?period=${fetchPeriod}&interval=${fetchInterval}`;
    try {
      const resp = await fetch(url);
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.error || !data.candles?.length) return;

      // If chart was replaced while we were fetching, abort — don't touch the disposed chart
      if (chartRef.current !== chart) return;

      const candle = chart.addSeries(CandlestickSeries, {
        upColor: "#26a69a", downColor: "#ef5350",
        borderUpColor: "#26a69a", borderDownColor: "#ef5350",
        wickUpColor: "#26a69a", wickDownColor: "#ef5350",
      });
      candle.setData(data.candles);
      candleRef.current = candle;

      // Snapshot the latest candle + bar interval for live-tick streaming.
      // The interval is inferred from the spacing of the last two bars (in
      // seconds): 5m = 300, 15m = 900, daily = 86400. With this, an arriving
      // live_price tick can either MUTATE the current bar (high/low/close)
      // or START a new one when the bar boundary rolls.
      const cs = data.candles;
      if (cs.length) {
        const last = cs[cs.length - 1];
        lastCandleRef.current = {
          time: Number(last.time),
          open: Number(last.open),
          high: Number(last.high),
          low:  Number(last.low),
          close:Number(last.close),
        };
        if (cs.length >= 2) {
          const dt = Number(cs[cs.length - 1].time) - Number(cs[cs.length - 2].time);
          if (dt > 0 && dt < 30 * 86400) barIntervalSecRef.current = dt;
        }
      }

      // ── Past prediction markers (+ for CALL, − for PUT) ───────────────
      // Pulls every prior signal we logged for this symbol and pins a marker
      // at its entry bar. Resolved wins glow brighter, losses are dimmed,
      // pending trades stay neutral. Lets the user see the AI's track record
      // overlaid on the same chart they're analyzing.
      try {
        const histRes = await fetch(`${API_BASE}/accuracy/${sym}`);
        if (histRes.ok && chartRef.current === chart) {
          const histData = await histRes.json();
          const history: any[] = histData?.history || [];
          if (history.length && data.candles?.length) {
            const candleTimes: number[] = data.candles.map((c: any) => c.time as number);
            const firstT = candleTimes[0];
            const lastT = candleTimes[candleTimes.length - 1];

            // Snap each prediction's wall-clock time to the nearest candle bar
            // so the marker actually lands on a real data point (lightweight-
            // charts will silently drop markers whose time isn't in the data).
            const snapToBar = (ts: number): number | null => {
              if (ts < firstT - 86400) return null;
              if (ts > lastT) return lastT;
              let lo = 0, hi = candleTimes.length - 1, best = candleTimes[0];
              while (lo <= hi) {
                const mid = (lo + hi) >> 1;
                const v = candleTimes[mid];
                if (v <= ts) { best = v; lo = mid + 1; } else { hi = mid - 1; }
              }
              return best;
            };

            // Dedupe by bar — each bar shows only ONE marker (the most recent
            // prediction). Without this, repeated analyses on the same day
            // pile a stack of "pending" labels onto the last candle and the
            // chart turns into spaghetti at the right edge.
            const byBar = new Map<number, any>();
            const sortedHist = [...history].sort((a, b) => {
              const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
              const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
              return ta - tb;
            });

            for (const p of sortedHist) {
              if (!p.signal || p.signal === "HOLD") continue;
              const created = p.created_at ? new Date(p.created_at).getTime() / 1000 : NaN;
              if (!Number.isFinite(created)) continue;
              const t = snapToBar(Math.floor(created));
              if (t === null) continue;

              const resolved = p.outcome != null;
              const isCall = p.signal === "BUY_CALL";
              const won = p.was_correct === 1;
              const lost = p.was_correct === 0 && resolved;

              // Resolved trades are bright (win bright / loss dim). Pending
              // trades render as a tiny faint arrow with NO text label, so
              // they stop crowding the chart with "pending" noise.
              const baseCall = won ? "#10b981" : lost ? "rgba(16,185,129,0.35)" : "rgba(16,185,129,0.45)";
              const basePut  = won ? "#ef4444" : lost ? "rgba(239,68,68,0.35)"  : "rgba(239,68,68,0.45)";
              const color = isCall ? baseCall : basePut;

              const entry = p.entry_price != null ? `$${Number(p.entry_price).toFixed(2)}` : "";
              // Only resolved trades get a price+result label. Pending ones
              // are visual-only (the arrow itself) — no "pending" string.
              const text = resolved ? `${isCall ? "+" : "−"} ${entry} ${won ? "WIN" : "LOSS"}` : "";

              byBar.set(t as number, {
                time: t as UTCTimestamp,
                position: isCall ? "belowBar" : "aboveBar",
                color,
                shape: isCall ? "arrowUp" : "arrowDown",
                text,
                size: 1,
              });
            }

            // Cap markers to the 25 most recent and only render. Sorted
            // ascending for the markers primitive.
            const markers = Array.from(byBar.values())
              .sort((a, b) => (a.time as number) - (b.time as number));
            const trimmed = markers.slice(-25);

            if (trimmed.length) {
              historyMarkersRef.current = createSeriesMarkers(candle, trimmed);
            }
          }
        }
      } catch (e) {
        console.warn("history markers load failed", e);
      }

      // EMA lines
      if (indicators_visible.ema && data.ema?.length) {
        const ema9 = chart.addSeries(LineSeries, { color: "#f59e0b", lineWidth: 1, title: "EMA9" });
        ema9.setData(data.ema.filter((d: any) => d.ema9 != null).map((d: any) => ({ time: d.time, value: d.ema9 })));
        const ema21 = chart.addSeries(LineSeries, { color: "#3b82f6", lineWidth: 1, title: "EMA21" });
        ema21.setData(data.ema.filter((d: any) => d.ema21 != null).map((d: any) => ({ time: d.time, value: d.ema21 })));
        const ema50 = chart.addSeries(LineSeries, { color: "#8b5cf6", lineWidth: 1, title: "EMA50" });
        ema50.setData(data.ema.filter((d: any) => d.ema50 != null).map((d: any) => ({ time: d.time, value: d.ema50 })));
      }

      // Bollinger Bands
      if (indicators_visible.bb && data.bb?.length) {
        const bbu = chart.addSeries(LineSeries, { color: "rgba(148,163,184,0.4)", lineWidth: 1, lineStyle: 2 });
        bbu.setData(data.bb.filter((d: any) => d.upper != null).map((d: any) => ({ time: d.time, value: d.upper })));
        const bbm = chart.addSeries(LineSeries, { color: "rgba(148,163,184,0.3)", lineWidth: 1, lineStyle: 2 });
        bbm.setData(data.bb.filter((d: any) => d.mid != null).map((d: any) => ({ time: d.time, value: d.mid })));
        const bbl = chart.addSeries(LineSeries, { color: "rgba(148,163,184,0.4)", lineWidth: 1, lineStyle: 2 });
        bbl.setData(data.bb.filter((d: any) => d.lower != null).map((d: any) => ({ time: d.time, value: d.lower })));
      }

      // VWAP
      if (indicators_visible.vwap && data.vwap?.length) {
        const vwapLine = chart.addSeries(LineSeries, { color: "#f97316", lineWidth: 1, lineStyle: 1, title: "VWAP" });
        vwapLine.setData(data.vwap.filter((d: any) => d.value != null).map((d: any) => ({ time: d.time, value: d.value })));
      }

      // SuperTrend — green when uptrend, red when downtrend
      if (indicators_visible.st && data.supertrend?.length) {
        const stUp = chart.addSeries(LineSeries, { color: "#10b981", lineWidth: 2, title: "ST↑" });
        const stDn = chart.addSeries(LineSeries, { color: "#ef4444", lineWidth: 2, title: "ST↓" });
        const upData = data.supertrend
          .filter((d: any) => d.value != null && d.direction === "up")
          .map((d: any) => ({ time: d.time as UTCTimestamp, value: d.value }));
        const dnData = data.supertrend
          .filter((d: any) => d.value != null && d.direction === "down")
          .map((d: any) => ({ time: d.time as UTCTimestamp, value: d.value }));
        if (upData.length) stUp.setData(upData);
        if (dnData.length) stDn.setData(dnData);
      }

      // Volume buy/sell histogram — green = buying pressure, red = selling pressure
      if (indicators_visible.vol && data.volume?.length) {
        chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.05, bottom: 0.28 } });
        const volSeries = chart.addSeries(HistogramSeries, {
          priceFormat: { type: 'volume' },
          priceScaleId: 'vol',
          lastValueVisible: false,
          priceLineVisible: false,
        } as any);
        (chart.priceScale('vol') as any).applyOptions({ scaleMarginTop: 0.78, scaleMarginBottom: 0, borderVisible: false });
        volSeries.setData(data.volume.map((d: any) => ({
          time: d.time as UTCTimestamp,
          value: d.value,
          color: d.color === "#26a69a" ? "rgba(38,166,154,0.6)" : "rgba(239,83,80,0.6)",
        })));
        // Lift the series + the latest bar so live ticks can mutate it.
        volSeriesRef.current = volSeries;
        const lv = data.volume[data.volume.length - 1];
        lastVolBarRef.current = { time: Number(lv.time), value: Number(lv.value) || 0 };
        prevDayVolRef.current = 0;  // reset; first live tick seeds the baseline
        // 20-bar rolling average for spike detection. Excludes the current
        // (incomplete) bar so a partial bar can't drag the baseline down.
        const tail = data.volume.slice(-21, -1) as any[];
        if (tail.length >= 5) {
          const sum = tail.reduce((s, d) => s + (Number(d.value) || 0), 0);
          avgVol20Ref.current = sum / tail.length;
        } else {
          avgVol20Ref.current = 0;
        }
        spikeMarkerListRef.current = [];
        spikeMarkersRef.current = null;
        spikeAlertedTimeRef.current = 0;
      } else {
        volSeriesRef.current = null;
        lastVolBarRef.current = null;
        prevDayVolRef.current = 0;
        avgVol20Ref.current = 0;
        spikeMarkerListRef.current = [];
        spikeMarkersRef.current = null;
      }

      chart.timeScale().fitContent();
    } catch (e) {
      console.error("Chart load error", e);
    }
  }, [indicators_visible]);

  const fetchChain = useCallback(async (sym: string, expiry = "") => {
    setChainLoading(true);
    try {
      const url = `/api/options-chain/${sym}${expiry ? `?expiry=${encodeURIComponent(expiry)}` : ""}`;
      const res = await fetch(url);
      const data: OptionsChain = await res.json();
      setChain(data);
      if (!expiry && data.selected_expiry) setChainExpiry(data.selected_expiry);
    } catch (e) {
      console.error("Options chain fetch error", e);
    } finally {
      setChainLoading(false);
    }
  }, []);

  const drawPrediction = useCallback((j: Judgment) => {
    // Capture the chart instance ONCE so all subsequent ops target the same
    // chart — avoids the race where the ref is replaced by a parallel
    // loadChart() mid-draw and we end up calling addSeries() on a disposed
    // chart object.
    const chart = chartRef.current;
    if (!chart || !j.forecast_line?.length) return;
    const st = signalStyle(j.signal);

    // Remove old forecast (defensive try/catch — chart may have been disposed
    // between the capture above and this point if a re-render fires)
    if (forecastRef.current) { try { chart.removeSeries(forecastRef.current); } catch {} forecastRef.current = null; }
    if (postForecastRef.current) { try { chart.removeSeries(postForecastRef.current); } catch {} postForecastRef.current = null; }
    if (targetLineRef.current) { try { chart.removeSeries(targetLineRef.current); } catch {} targetLineRef.current = null; }
    if (stopLineRef.current) { try { chart.removeSeries(stopLineRef.current); } catch {} stopLineRef.current = null; }

    if (j.signal === "HOLD") return;

    // Bail if the chart was swapped while we cleaned up old series
    if (chartRef.current !== chart) return;

    // ── Main prediction line — solid + thick + titled "PREDICTION"
    // Solid (not dashed) so it looks like a real continuation of the price
    // chart; the title legend tells the user which line is the model's call.
    try {
    const forecast = chart.addSeries(LineSeries, {
      color: st.hex, lineWidth: 3, lineStyle: 0,
      title: `PREDICTION (${j.signal === "BUY_CALL" ? "↑ CALL" : "↓ PUT"})`,
      crosshairMarkerVisible: true, lastValueVisible: true,
    });
    forecast.setData(j.forecast_line.map(p => ({ time: p.time as UTCTimestamp, value: p.value })));
    forecastRef.current = forecast;

    // Mark the start of the prediction so it's obvious where "now" ends and
    // the model's projection begins.
    const startTs = j.forecast_line[0].time as UTCTimestamp;
    createSeriesMarkers(forecast, [{
      time: startTs, position: "inBar", color: st.hex, shape: "circle",
      text: "PREDICTION START",
    }]);

    // ── Post-prediction line — what the model thinks happens AFTER target/stop
    // Distinct color (amber) + dashed so it reads as "this is what's likely
    // after the trade closes — sell here, don't hold". The legend title
    // includes the mode so the trader instantly sees if it's a continuation
    // or a reversion.
    if (j.post_forecast_line?.length) {
      const modeLabel =
        j.post_forecast_mode === "continuation" ? "CONTINUES ↑" :
        j.post_forecast_mode === "reversion"    ? "PULLS BACK ↓" :
                                                   "DRIFTS";
      const postColor = j.post_forecast_mode === "continuation" ? st.hex
                       : j.post_forecast_mode === "reversion" ? "#f59e0b"
                       : "#94a3b8";
      // Score badge in the title — keeps the legend honest about HOW strong
      // the follow-through score is for THIS ticker (different per stock).
      const scoreTxt = typeof j.post_forecast_score === "number"
        ? ` (${j.post_forecast_score >= 0 ? "+" : ""}${j.post_forecast_score.toFixed(2)})`
        : "";
      const post = chart.addSeries(LineSeries, {
        color: postColor, lineWidth: 2, lineStyle: 2,
        title: `AFTER PREDICTION — ${modeLabel}${scoreTxt}`,
        crosshairMarkerVisible: true, lastValueVisible: false,
      });
      // Bridge from the last forecast point so the lines visually connect
      const bridged = [
        { time: j.forecast_line[j.forecast_line.length - 1].time as UTCTimestamp,
          value: j.forecast_line[j.forecast_line.length - 1].value },
        ...j.post_forecast_line.map(p => ({ time: p.time as UTCTimestamp, value: p.value })),
      ];
      post.setData(bridged);
      postForecastRef.current = post;
    }

    // ── Target & stop horizontal levels (now span the full forecast incl. post)
    const lastWindowTs = (j.post_forecast_line?.length
      ? j.post_forecast_line[j.post_forecast_line.length - 1].time
      : j.forecast_line[j.forecast_line.length - 1].time) as UTCTimestamp;

    if (j.target_price) {
      const tgt = chart.addSeries(LineSeries, {
        color: "#10b981", lineWidth: 1, lineStyle: 1, title: `TARGET $${j.target_price}`,
      });
      const ts0 = j.forecast_line[0].time as UTCTimestamp;
      tgt.setData([{ time: ts0, value: j.target_price }, { time: lastWindowTs, value: j.target_price }]);
      createSeriesMarkers(tgt, [{ time: lastWindowTs, position: "aboveBar", color: "#10b981", shape: "arrowUp", text: `TARGET $${j.target_price}` }]);
      targetLineRef.current = tgt;
    }
    if (j.stop_loss) {
      const stp = chart.addSeries(LineSeries, {
        color: "#ef4444", lineWidth: 1, lineStyle: 1, title: `STOP $${j.stop_loss}`,
      });
      const ts0 = j.forecast_line[0].time as UTCTimestamp;
      stp.setData([{ time: ts0, value: j.stop_loss }, { time: lastWindowTs, value: j.stop_loss }]);
      createSeriesMarkers(stp, [{ time: lastWindowTs, position: "belowBar", color: "#ef4444", shape: "arrowDown", text: `STOP $${j.stop_loss}` }]);
      stopLineRef.current = stp;
    }
    try { chart.timeScale().scrollToRealTime(); } catch {}
    } catch (err) {
      // Chart was disposed mid-draw (parallel loadChart fired). Safe to ignore —
      // the next drawPrediction on the freshly-built chart will redraw cleanly.
      console.debug("drawPrediction: chart disposed mid-draw, skipping", err);
    }
  }, []);

  // ── Initial chart load + reload on toggle of any indicator ───────────
  useEffect(() => {
    loadChart(symbol, period).then(() => {
      if (judgment) drawPrediction(judgment);
    });
    const resize = () => {
      if (chartRef.current && chartContainerRef.current) {
        try {
          chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
        } catch {}
      }
    };
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [symbol, period, indicators_visible]);

  // ── Redraw prediction when judgment arrives ───────────────────────────
  // CHANGED: just overlay the prediction on the existing chart instead of
  // tearing the whole chart down. Rebuilding the entire chart on every
  // judgment update was racing against the WebSocket-handler's rebuild
  // (and against this effect re-firing while a prior load was still in
  // flight) which is what produced the "Object is disposed" overlay.
  useEffect(() => {
    if (!judgment) return;
    if (chartRef.current) {
      drawPrediction(judgment);
    }
  }, [judgment, drawPrediction]);

  // ── Load Fear & Greed and political news ──────────────────────────────
  const loadFearGreed = useCallback(async (force = false) => {
    setFearLoading(true);
    try {
      const [fgRes, polRes] = await Promise.all([
        fetch(`${API_BASE}/fear-greed${force ? "?nocache=1" : ""}`),
        fetch(`${API_BASE}/political-news`),
      ]);
      if (fgRes.ok) { const d = await fgRes.json(); if (!d.error) setFearGreed(d); }
      if (polRes.ok) { const d = await polRes.json(); setPoliticalNews(d.news || []); }
    } catch {}
    setFearLoading(false);
  }, []);

  // ── Load per-stock sentiment (changes per ticker) ─────────────────────
  const loadStockSentiment = useCallback(async (sym: string) => {
    if (!sym) return;
    try {
      const res = await fetch(`${API_BASE}/stock-sentiment/${sym}`);
      if (res.ok) {
        const d: StockSentiment = await res.json();
        if (!(d as any).error) setStockSentiment(d);
      }
    } catch {}
  }, []);

  // ── Symbol search (autocomplete) ──────────────────────────────────────
  const runSearch = useCallback((q: string) => {
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    if (!q || q.length < 1) {
      setSearchResults([]);
      setSearchOpen(false);
      setSearchLoading(false);
      return;
    }
    searchDebounceRef.current = setTimeout(async () => {
      if (searchAbortRef.current) searchAbortRef.current.abort();
      const ctrl = new AbortController();
      searchAbortRef.current = ctrl;
      setSearchLoading(true);
      try {
        const res = await fetch(`${API_BASE}/search?q=${encodeURIComponent(q)}&limit=8`, { signal: ctrl.signal });
        if (res.ok) {
          const d = await res.json();
          setSearchResults(d.results || []);
          setSearchOpen((d.results || []).length > 0);
          setSearchActiveIdx(-1);
        }
      } catch (e: any) {
        if (e?.name !== "AbortError") console.error("Search error", e);
      } finally {
        setSearchLoading(false);
      }
    }, 200);
  }, []);

  // Close suggestion dropdown when clicking outside the search box
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (searchBoxRef.current && !searchBoxRef.current.contains(e.target as Node)) {
        setSearchOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // ── Load accuracy report ───────────────────────────────────────────────
  const loadAccuracy = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/accuracy`);
      if (res.ok) { const d = await res.json(); if (d.report) setAccuracy(d.report); }
    } catch {}
  }, []);

  // ── Load learning status (DB rows, backups, per-agent accumulation) ────
  // This proves to the user that the AI is actually persisting what it learns
  // and that their data is backed up against accidental file resets.
  const loadLearning = useCallback(async () => {
    try {
      const [statusRes, eventsRes] = await Promise.all([
        fetch(`${API_BASE}/learning-status`),
        fetch(`${API_BASE}/learning-events?limit=30`),
      ]);
      if (statusRes.ok) { const d = await statusRes.json(); setLearning(d); }
      if (eventsRes.ok) {
        const d = await eventsRes.json();
        setLearningEvents(Array.isArray(d.events) ? d.events : []);
      }
    } catch {}
  }, []);

  const triggerBackup = useCallback(async () => {
    setBackupBusy(true);
    try {
      const res = await fetch(`${API_BASE}/learning-backup`, { method: "POST" });
      if (res.ok) await loadLearning();
    } catch {}
    setBackupBusy(false);
  }, [loadLearning]);

  // ── Paper trading helpers ──────────────────────────────────────────────
  const showPaperToast = (msg: string) => {
    setPaperToast(msg);
    setTimeout(() => setPaperToast(""), 4000);
  };
  const loadPaperData = useCallback(async () => {
    try {
      setPaperLoading(true);
      const [acctRes, openRes, histRes] = await Promise.all([
        fetch(`${API_BASE}/paper/account`),
        fetch(`${API_BASE}/paper/positions?status=open`),
        fetch(`${API_BASE}/paper/positions?status=closed`),
      ]);
      if (acctRes.ok) setPaperAccount(await acctRes.json());
      if (openRes.ok) {
        const d = await openRes.json();
        setPaperOpen(d.positions || []);
        if (d.auto_closed?.length) {
          const c = d.auto_closed[0];
          showPaperToast(`Auto-closed ${c.symbol} (${c.close_reason}) → P/L $${(c.pnl || 0).toFixed(2)}`);
        }
      }
      if (histRes.ok) { const d = await histRes.json(); setPaperHistory(d.positions || []); }
    } finally { setPaperLoading(false); }
  }, []);
  const tradeCurrentSignal = useCallback(async () => {
    if (!judgment || judgment.signal === "HOLD") return;
    try {
      setPaperLoading(true);
      const res = await fetch(`${API_BASE}/paper/open`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol, signal: judgment.signal, horizon,
          entry: judgment.entry_price,
          target: judgment.target_price,
          stop: judgment.stop_loss,
          confidence: judgment.confidence,
        }),
      });
      const data = await res.json();
      if (data.error) { showPaperToast(`Trade failed: ${data.error}`); }
      else {
        showPaperToast(`Opened ${data.signal === "BUY_CALL" ? "CALL" : "PUT"} on ${data.symbol} @ $${data.entry_price.toFixed(2)}`);
        await loadPaperData();
      }
    } catch (e: any) { showPaperToast(`Trade failed: ${e?.message || e}`); }
    finally { setPaperLoading(false); }
  }, [judgment, symbol, horizon, loadPaperData]);
  const clearPredictionOverlay = useCallback(() => {
    if (!chartRef.current) return;
    for (const ref of [forecastRef, postForecastRef, targetLineRef, stopLineRef]) {
      if (ref.current) {
        try { chartRef.current.removeSeries(ref.current); } catch {}
        ref.current = null;
      }
    }
  }, []);
  const closePaperPosition = useCallback(async (id: number) => {
    const closingPos = paperOpen.find(p => p.id === id);
    // Optimistically hide it from the open list so the UI never feels stuck
    setPaperOpen(prev => prev.filter(p => p.id !== id));
    try {
      setPaperLoading(true);
      const res = await fetch(`${API_BASE}/paper/close/${id}`, { method: "POST" });
      const data = await res.json();
      if (data.error) {
        showPaperToast(data.error);
        // Roll back the optimistic removal
        await loadPaperData();
        return;
      }
      showPaperToast(`Closed ${data.symbol} → P/L $${(data.pnl || 0).toFixed(2)}`);
      // Wipe the prediction overlay (target / stop / forecast lines) for the
      // closed symbol so the chart reflects that the trade is over.
      if (closingPos?.symbol === symbol) {
        clearPredictionOverlay();
        setJudgment(null);
      }
      await loadPaperData();
    } catch (e: any) {
      showPaperToast(`Close failed: ${e?.message || e}`);
      await loadPaperData();
    } finally { setPaperLoading(false); }
  }, [loadPaperData, paperOpen, symbol, clearPredictionOverlay]);
  const resetPaperAccount = useCallback(async () => {
    if (!confirm("Reset paper account to $10,000 and close all positions?")) return;
    setPaperLoading(true);
    try {
      await fetch(`${API_BASE}/paper/reset`, { method: "POST" });
      // Reset closes everything — clear the chart overlay too
      clearPredictionOverlay();
      setJudgment(null);
      await loadPaperData();
      showPaperToast("Account reset to $10,000");
    } finally { setPaperLoading(false); }
  }, [loadPaperData, clearPredictionOverlay]);

  useEffect(() => {
    loadFearGreed();
    loadAccuracy();
    loadLearning();
    // Fetch the supported prediction horizons once
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/horizons`);
        if (res.ok) {
          const d = await res.json();
          if (Array.isArray(d.horizons) && d.horizons.length) {
            setHorizons(d.horizons);
          }
        }
      } catch {}
    })();
    loadPaperData();
    const iv = setInterval(() => { loadFearGreed(); loadAccuracy(); loadLearning(); }, 120000);
    return () => clearInterval(iv);
  }, []);

  // Live mark-to-market refresh while on the Paper tab
  useEffect(() => {
    if (tab !== "paper") return;
    loadPaperData();
    const iv = setInterval(loadPaperData, 20000);
    return () => clearInterval(iv);
  }, [tab, loadPaperData]);

  // ── WebSocket analysis ────────────────────────────────────────────────
  const runAnalysis = useCallback((sym: string) => {
    if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
    setVotes([]);
    setJudgment(null);
    setStockSentiment(null);
    setStatus("");
    setAnalyzing(true);

    const ws = new WebSocket(`${WS_BASE}/api/ws/analyze/${sym}?horizon=${horizonRef.current}`);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "live_price") {
        setLivePrice(msg);
        // ── Stream the tick into the latest candle so the chart actually
        // moves with the market instead of being a static snapshot. If the
        // tick arrived inside the current bar's window, mutate close + extend
        // high/low. If a new bar window has started, append a fresh candle.
        const px = Number(msg.price);
        const cR = candleRef.current;
        const last = lastCandleRef.current;
        if (cR && last && Number.isFinite(px) && px > 0) {
          const intv = barIntervalSecRef.current || 86400;
          const nowSec = Math.floor(Date.now() / 1000);
          const barStart = Math.floor(nowSec / intv) * intv;
          if (barStart > last.time) {
            // Roll a new bar — opens at previous close, no high/low yet
            const fresh = { time: barStart, open: last.close, high: px, low: px, close: px };
            try { cR.update(fresh); } catch {}
            lastCandleRef.current = fresh;
          } else {
            // Mutate the current bar in place
            const upd = {
              time: last.time,
              open: last.open,
              high: Math.max(last.high, px),
              low:  Math.min(last.low,  px),
              close: px,
            };
            try { cR.update(upd); } catch {}
            lastCandleRef.current = upd;
          }

          // ── Live volume bar — ticks alongside the candle so the user can
          // see if a price move is happening on real volume or thin tape.
          // Daily bars: bar value = cumulative day volume (always growing).
          // Intraday bars: increment by the delta since the previous tick;
          // start a fresh bar at 0 when the bar boundary rolls.
          const vS = volSeriesRef.current;
          const lvBar = lastVolBarRef.current;
          const dayVol = Number(msg.day_volume) || 0;
          if (vS && lvBar && dayVol > 0) {
            const cur = lastCandleRef.current!;
            const isUp = cur.close >= cur.open;
            const color = isUp ? "rgba(38,166,154,0.7)" : "rgba(239,83,80,0.7)";
            let newVal = lvBar.value;
            let newTime = lvBar.time;

            if (intv >= 86400) {
              // Daily — just mirror the cumulative day volume
              newTime = cur.time;
              newVal = dayVol;
            } else {
              // Intraday — accumulate delta into the current bar; start fresh
              // when the bar rolls
              const prev = prevDayVolRef.current;
              const delta = prev > 0 ? Math.max(0, dayVol - prev) : 0;
              if (cur.time > lvBar.time) {
                newTime = cur.time;
                newVal = delta;
              } else {
                newVal = lvBar.value + delta;
              }
            }
            prevDayVolRef.current = dayVol;
            try { vS.update({ time: newTime as any, value: newVal, color }); } catch {}
            lastVolBarRef.current = { time: newTime, value: newVal };

            // ── Volume-spike alert ──────────────────────────────────────
            // Fire when the current bar's running volume crosses 2× the
            // 20-bar average AND the alert hasn't already fired for this
            // bar. The marker stays on the chart; the banner fades in 6s.
            const avg = avgVol20Ref.current;
            if (avg > 0 && newVal >= avg * 2 && spikeAlertedTimeRef.current !== newTime) {
              spikeAlertedTimeRef.current = newTime;
              const ratio = newVal / avg;

              // Banner overlay (auto-fade)
              setSpikeAlert({ ratio, ts: newTime });
              if (spikeTimerRef.current) clearTimeout(spikeTimerRef.current);
              spikeTimerRef.current = setTimeout(() => setSpikeAlert(null), 6000);

              // Persistent marker on the candle
              try {
                const cR2 = candleRef.current;
                if (cR2) {
                  spikeMarkerListRef.current.push({
                    time: newTime as UTCTimestamp,
                    position: "aboveBar",
                    color: "#fbbf24",
                    shape: "circle",
                    text: `VOL ${ratio.toFixed(1)}×`,
                    size: 1,
                  });
                  spikeMarkerListRef.current.sort(
                    (a, b) => (a.time as number) - (b.time as number)
                  );
                  spikeMarkersRef.current = createSeriesMarkers(
                    cR2,
                    spikeMarkerListRef.current
                  );
                }
              } catch {}
            }
          }
        }
      }
      else if (msg.type === "status") setStatus(msg.message);
      else if (msg.type === "judgment") {
        if (msg.votes?.length) setVotes(msg.votes);
        const j: Judgment = msg.judgment;
        if (msg.indicators) setIndicators(msg.indicators);
        setJudgment(j);
        if (msg.accuracy) setAccuracy(msg.accuracy);
        if (msg.stock_sentiment) setStockSentiment(msg.stock_sentiment);
        setAnalyzing(false);
        setStatus("");
        setTab("signal");
        loadFearGreed();
        // Note: setJudgment(j) above triggers the [judgment] effect which
        // calls drawPrediction. Don't rebuild the chart here — that race was
        // the source of the "Object is disposed" runtime overlay.
        if (chartRef.current) {
          drawPrediction(j);
        } else {
          // Edge case: judgment arrived before the initial chart finished
          // building. Trigger a load and overlay once it's ready.
          loadChart(symRef.current, periodRef.current).then(() => drawPrediction(j));
        }
      } else if (msg.type === "error") {
        setStatus(`❌ ${msg.message}`);
        setAnalyzing(false);
      }
    };
    ws.onerror = () => { setStatus("❌ WebSocket error"); setAnalyzing(false); };
    ws.onclose = () => { if (analyzing) setAnalyzing(false); };
  }, []);

  const handleAnalyze = (overrideSym?: string) => {
    const s = (overrideSym ?? inputSym).trim().toUpperCase();
    if (!s) return;
    setInputSym(s);
    setSymbol(s);
    symRef.current = s;
    setSearchOpen(false);
    setSearchResults([]);
    runAnalysis(s);
  };

  const WATCHLIST = ["AAPL", "NVDA", "TSLA", "MSFT", "SPY", "QQQ", "AMZN", "META", "AMD", "COIN"];
  const TAB_CLASSES = (t: string) =>
    `relative flex-1 px-1 py-2.5 text-[10px] font-bold tracking-wider cursor-pointer whitespace-nowrap text-center transition-colors ${tab === t ? "text-white" : "text-slate-500 hover:text-slate-200"}`;

  const stj = judgment ? signalStyle(judgment.signal) : signalStyle("HOLD");
  const callPct = votes.length ? Math.round(votes.filter(v => v.vote === "BUY_CALL").length / votes.length * 100) : 0;
  const putPct  = votes.length ? Math.round(votes.filter(v => v.vote === "BUY_PUT").length  / votes.length * 100) : 0;

  return (
    <div className="flex flex-col h-screen bg-[#0a0e1a] text-white font-sans overflow-hidden">
      {/* ── Top Bar ── */}
      <div className="flex items-center gap-3 px-4 py-2 bg-[#0f172a] border-b border-slate-700/50 shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-blue-600 flex items-center justify-center text-xs font-black">T</div>
          <div>
            <div className="text-sm font-bold leading-tight">TradeSignal AI</div>
            <div className="text-xs text-slate-500">9-AGENT · CALL/PUT · REAL-TIME</div>
          </div>
        </div>
        <div ref={searchBoxRef} className="relative">
          <input
            className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5 text-sm font-mono w-44 focus:outline-none focus:border-blue-500"
            value={inputSym}
            onChange={e => {
              const v = e.target.value.toUpperCase();
              setInputSym(v);
              runSearch(v);
            }}
            onFocus={() => { if (searchResults.length > 0) setSearchOpen(true); }}
            onKeyDown={e => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setSearchActiveIdx(i => Math.min(i + 1, searchResults.length - 1));
                if (!searchOpen && searchResults.length) setSearchOpen(true);
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setSearchActiveIdx(i => Math.max(i - 1, -1));
              } else if (e.key === "Escape") {
                setSearchOpen(false);
              } else if (e.key === "Enter") {
                if (searchOpen && searchActiveIdx >= 0 && searchResults[searchActiveIdx]) {
                  handleAnalyze(searchResults[searchActiveIdx].symbol);
                } else {
                  handleAnalyze();
                }
              }
            }}
            placeholder="Search ticker or name…"
            autoComplete="off"
          />
          {searchOpen && (searchResults.length > 0 || searchLoading) && (
            <div className="absolute left-0 top-full mt-1 w-72 max-h-80 overflow-y-auto bg-slate-900 border border-slate-700 rounded-lg shadow-2xl z-50">
              {searchLoading && searchResults.length === 0 && (
                <div className="px-3 py-2 text-xs text-slate-500">Searching…</div>
              )}
              {searchResults.map((r, i) => (
                <button
                  key={`${r.symbol}-${i}`}
                  onMouseDown={(e) => { e.preventDefault(); handleAnalyze(r.symbol); }}
                  onMouseEnter={() => setSearchActiveIdx(i)}
                  className={`w-full text-left px-3 py-2 flex items-center gap-2 border-b border-slate-800 last:border-0 ${
                    i === searchActiveIdx ? "bg-slate-800" : "hover:bg-slate-800/60"
                  }`}
                >
                  <span className="font-mono font-bold text-sm text-blue-400 w-16 shrink-0">{r.symbol}</span>
                  <span className="flex-1 min-w-0">
                    <span className="block text-xs text-slate-200 truncate">{r.name || "—"}</span>
                    <span className="block text-xs text-slate-500">{r.exchange}{r.type ? ` · ${r.type}` : ""}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          onClick={() => handleAnalyze()}
          disabled={analyzing}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-4 py-1.5 rounded-lg text-sm font-bold transition-all"
        >
          {analyzing ? "Analyzing..." : "Analyze"}
        </button>

        <div className="flex-1 flex flex-wrap gap-1.5 justify-center">
          {WATCHLIST.map(s => (
            <button key={s}
              onClick={() => { setInputSym(s); setSymbol(s); symRef.current = s; runAnalysis(s); }}
              className={`px-2.5 py-1 rounded text-xs font-semibold transition-all ${symbol === s ? "bg-blue-600 text-white" : "bg-slate-800 text-slate-400 hover:bg-slate-700"}`}
            >{s}</button>
          ))}
        </div>

        {livePrice && (
          <div className="text-right shrink-0">
            <div className="text-base font-bold font-mono">${livePrice.price.toFixed(2)}</div>
            <div className={`text-xs font-semibold ${livePrice.change_pct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              {livePrice.change_pct >= 0 ? "▲" : "▼"} {Math.abs(livePrice.change_pct).toFixed(2)}%
            </div>
          </div>
        )}
      </div>

      {/* ── Prediction Horizon selector (drives the agents + forecast window) ── */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-800 shrink-0 bg-[#0c1322]">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 font-bold shrink-0">
          Prediction length:
        </span>
        <div className="flex flex-wrap gap-1.5">
          {(horizons.length ? horizons : [
            { key: "intraday", label: "Intraday (1–2h)" } as Horizon,
            { key: "day",      label: "Today (0DTE)" }     as Horizon,
            { key: "swing",    label: "Swing (1–5d)" }     as Horizon,
            { key: "position", label: "Position (1–3w)" }  as Horizon,
          ]).map(h => {
            const active = horizon === h.key;
            return (
              <button
                key={h.key}
                onClick={() => {
                  setHorizon(h.key);
                  horizonRef.current = h.key;
                  if (symRef.current) runAnalysis(symRef.current);
                }}
                title={h.expiry_pref ? `Best for ${h.expiry_pref} options` : undefined}
                className={`text-xs px-3 py-1 rounded font-semibold transition-all ${
                  active
                    ? "bg-emerald-600 text-white shadow-sm shadow-emerald-500/30"
                    : "bg-slate-800 text-slate-400 hover:bg-slate-700 hover:text-white"
                }`}
              >{h.label}</button>
            );
          })}
        </div>
        {judgment?.horizon && (
          <span className="ml-auto text-[10px] text-slate-500 italic shrink-0">
            current: {judgment.horizon.label} · need {judgment.horizon.threshold}/9 agents
          </span>
        )}
      </div>

      {/* ── Main Area ── */}
      <div className="flex flex-1 overflow-hidden">
        {/* ── LEFT: Chart ── */}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          {/* Chart window controls */}
          <div className="flex items-center gap-2 px-3 py-1.5 border-b border-slate-800 shrink-0 bg-[#0d1524]">
            <span className="text-[10px] uppercase tracking-wider text-slate-600 font-bold mr-1">Chart:</span>
            {(["1D","5D","1M","3M","6M"] as const).map(p => {
              const pMap: Record<string, string> = { "1D":"1d","5D":"5d","1M":"1mo","3M":"3mo","6M":"6mo" };
              return (
                <button key={p} onClick={() => { setPeriod(pMap[p]); periodRef.current = pMap[p]; }}
                  className={`text-xs px-2.5 py-1 rounded font-semibold ${period === pMap[p] ? "bg-blue-600 text-white" : "text-slate-500 hover:text-white"}`}
                >{p}</button>
              );
            })}
            <div className="w-px h-4 bg-slate-700 mx-1" />
            {(["ema","bb","vwap","st","vol"] as const).map(ind => (
              <button key={ind} onClick={() => setIndicatorsVisible(v => ({ ...v, [ind]: !v[ind] }))}
                className={`text-xs px-2.5 py-1 rounded font-semibold ${indicators_visible[ind] ? "bg-slate-600 text-white" : "text-slate-600 hover:text-slate-400"}`}
              >{ind.toUpperCase()}</button>
            ))}
          </div>

          {/* Chart */}
          <div className="flex-1 min-h-0 relative">
            <div ref={chartContainerRef} className="w-full h-full" />
            {spikeAlert && (
              <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 pointer-events-none">
                <div className="px-3 py-1.5 rounded-lg bg-amber-500/95 text-slate-900 text-xs font-bold shadow-lg shadow-amber-500/40 animate-pulse flex items-center gap-2">
                  <span>⚡ VOLUME SPIKE</span>
                  <span className="font-mono bg-slate-900/30 px-1.5 py-0.5 rounded">
                    {spikeAlert.ratio.toFixed(1)}× avg
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Status bar */}
          {(status || analyzing) && (
            <div className="px-3 py-1.5 bg-slate-900 border-t border-slate-800 text-xs text-slate-400 shrink-0 flex items-center gap-2">
              {analyzing && <span className="inline-block w-2 h-2 rounded-full bg-blue-400 animate-pulse" />}
              {status}
            </div>
          )}

          {/* Vote bar */}
          {votes.length > 0 && (
            <div className="px-3 py-2 bg-[#0d1524] border-t border-slate-800 shrink-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs text-slate-500">{votes.length}/9 agents voted</span>
                <span className="text-xs text-emerald-400">▲CALL {callPct}%</span>
                <span className="text-xs text-red-400">▼PUT {putPct}%</span>
              </div>
              <div className="flex h-2 rounded-full overflow-hidden gap-0.5">
                {votes.map((v, i) => (
                  <div key={i} className={`flex-1 rounded-sm ${v.vote === "BUY_CALL" ? "bg-emerald-500" : v.vote === "BUY_PUT" ? "bg-red-500" : "bg-slate-600"}`} />
                ))}
                {Array.from({ length: Math.max(0, 9 - votes.length) }).map((_, i) => (
                  <div key={`empty-${i}`} className="flex-1 rounded-sm bg-slate-800 animate-pulse" />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── RIGHT: Sidebar ── */}
        <div className="w-80 lg:w-96 flex flex-col border-l border-slate-700/50 bg-[#0d1524] shrink-0 overflow-hidden">
          {/* Fear & Greed mini strip — market-wide AND per-stock */}
          {(fearGreed || stockSentiment) && (
            <div className="px-3 py-2 border-b border-slate-800 shrink-0 space-y-2">
              {fearGreed && (
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-slate-500 font-semibold">MARKET F&G</span>
                    <span className="font-bold" style={{ color: fearGreed.color }}>{fearGreed.score} — {fearGreed.label}</span>
                  </div>
                  <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden relative">
                    <div className="h-full rounded-full transition-all" style={{ width: `${fearGreed.score}%`, background: `linear-gradient(90deg, #ef4444, #f97316, #f59e0b, #22c55e, #10b981)` }} />
                  </div>
                </div>
              )}
              {stockSentiment && (
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-slate-500 font-semibold">{stockSentiment.symbol} SENTIMENT</span>
                    <span className="font-bold" style={{ color: stockSentiment.color }}>{stockSentiment.score} — {stockSentiment.label}</span>
                  </div>
                  <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden relative">
                    <div className="h-full rounded-full transition-all" style={{ width: `${stockSentiment.score}%`, background: `linear-gradient(90deg, #ef4444, #f97316, #f59e0b, #22c55e, #10b981)` }} />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Tabs — single horizontal row with an underline indicator */}
          <div className="flex items-stretch border-b border-slate-800 shrink-0">
            {([
              ["signal","SIGNAL"],["agents","AGENTS"],["options","OPTS"],["chain","CHAIN"],
              ["news","NEWS"],["fear","F&G"],["accuracy","STATS"],["paper","PAPER"]
            ] as const).map(([t, label]) => (
              <button key={t} onClick={() => {
                setTab(t);
                if (t === "chain" && symbol) fetchChain(symbol);
                if (t === "paper") loadPaperData();
              }} className={TAB_CLASSES(t)}>
                {label}
                {tab === t && (
                  <span className="absolute left-2 right-2 -bottom-px h-0.5 bg-blue-500 rounded-full" />
                )}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto p-3">

            {/* ── SIGNAL TAB ── */}
            {tab === "signal" && (
              <div className="space-y-4">
                {!judgment && !analyzing ? (
                  <div className="text-center py-8 space-y-2">
                    <p className="text-slate-400 text-sm">Enter a symbol and click Analyze</p>
                    <p className="text-slate-500 text-xs">9 agents will vote CALL / PUT / HOLD</p>
                    <p className="text-slate-600 text-xs">5 of 9 must agree to fire a signal</p>
                  </div>
                ) : analyzing ? (
                  <div className="text-center py-6 space-y-3">
                    <div className="w-12 h-12 border-4 border-blue-600 border-t-transparent rounded-full animate-spin mx-auto" />
                    <p className="text-slate-400 text-sm">{status || "Analyzing..."}</p>
                    <div className="flex flex-wrap gap-1.5 justify-center">
                      {votes.map((v, i) => (
                        <span key={i} className={`text-lg ${v.vote === "BUY_CALL" ? "text-emerald-400" : v.vote === "BUY_PUT" ? "text-red-400" : "text-slate-500"}`}>{v.emoji}</span>
                      ))}
                    </div>
                  </div>
                ) : judgment ? (
                  <>
                    <div className="text-center space-y-2">
                      <SignalBadge signal={judgment.signal} size="lg" />
                      {judgment.sticky && (
                        <div className="flex items-center justify-center gap-1.5 text-[10px] text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded-full px-2.5 py-0.5 mx-auto w-fit">
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect width="14" height="10" x="5" y="11" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
                          <span className="font-medium">{judgment.sticky.message}</span>
                        </div>
                      )}
                      <div className="mt-2">
                        <ConfRing
                          pct={judgment.target_hit_prob ?? judgment.confidence}
                          signal={judgment.signal}
                          label={judgment.signal === "HOLD" ? "neutral" : `chance to hit $${judgment.target_price.toFixed(2)}`}
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <Stat label="CALL" value={`${judgment.vote_tally.BUY_CALL}/9`} color="text-emerald-400" />
                      <Stat label="PUT" value={`${judgment.vote_tally.BUY_PUT}/9`} color="text-red-400" />
                      <Stat label="HOLD" value={`${judgment.vote_tally.HOLD}/9`} color="text-slate-400" />
                    </div>
                    <div className={`rounded-xl border p-3 text-xs space-y-1.5 ${stj.bg} ${stj.border}`}>
                      <div className="flex justify-between"><span className="text-slate-400">Entry</span><span className="font-mono font-bold">${judgment.entry_price.toFixed(2)}</span></div>
                      <div className="flex justify-between"><span className="text-slate-400">Target</span><span className="font-mono font-bold text-emerald-400">${judgment.target_price.toFixed(2)}</span></div>
                      <div className="flex justify-between"><span className="text-slate-400">Stop</span><span className="font-mono font-bold text-red-400">${judgment.stop_loss.toFixed(2)}</span></div>
                      {judgment.entry_price > 0 && judgment.stop_loss > 0 && judgment.target_price > 0 && (
                        <div className="flex justify-between border-t border-slate-700 pt-1.5">
                          <span className="text-slate-400">R/R</span>
                          <span className="font-mono font-bold text-blue-400">
                            {Math.abs((judgment.target_price - judgment.entry_price) / (judgment.entry_price - judgment.stop_loss + 0.01)).toFixed(2)}:1
                          </span>
                        </div>
                      )}
                    </div>
                    {judgment.signal !== "HOLD" && (
                      <button
                        onClick={tradeCurrentSignal}
                        disabled={paperLoading}
                        className={`w-full py-2 rounded-lg text-sm font-bold transition-all disabled:opacity-50 ${
                          judgment.signal === "BUY_CALL"
                            ? "bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-500/20"
                            : "bg-red-600 hover:bg-red-500 text-white shadow-red-500/20"
                        } shadow-lg`}
                      >
                        Paper Trade This {judgment.signal === "BUY_CALL" ? "CALL" : "PUT"}
                      </button>
                    )}
                    {judgment.macro_context && (
                      <div className="bg-slate-800/50 rounded-lg p-2.5 space-y-1.5">
                        <div className="text-xs text-slate-500 font-semibold">MACRO CONTEXT</div>
                        <div className="grid grid-cols-4 gap-1.5 text-[11px]">
                          {(() => {
                            const adx = judgment.macro_context.adx;
                            const adxOk = adx >= 18;
                            return (
                              <div className={`flex flex-col items-center justify-center rounded px-1.5 py-1 border ${
                                adxOk ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                                      : "bg-red-500/10 border-red-500/40 text-red-300"
                              }`}>
                                <span className="text-[10px] uppercase opacity-70">ADX</span>
                                <span className="font-mono font-bold">{adx.toFixed(0)} {adxOk ? "✓" : "chop"}</span>
                              </div>
                            );
                          })()}
                          {(() => {
                            const w = judgment.macro_context.weekly_trend;
                            const arrow = w.dir === "up" ? "▲" : w.dir === "down" ? "▼" : "—";
                            const cls = w.dir === "up" ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                                      : w.dir === "down" ? "bg-red-500/10 border-red-500/30 text-red-300"
                                      : "bg-slate-700/30 border-slate-600/40 text-slate-400";
                            return (
                              <div className={`flex flex-col items-center justify-center rounded px-1.5 py-1 border ${cls}`}>
                                <span className="text-[10px] uppercase opacity-70">Weekly</span>
                                <span className="font-mono font-bold">{arrow} {w.strength.toFixed(1)}%</span>
                              </div>
                            );
                          })()}
                          {(() => {
                            const s = judgment.macro_context.spy_trend;
                            if (s.dir === "self") {
                              return (
                                <div className="flex flex-col items-center justify-center rounded px-1.5 py-1 border bg-slate-700/30 border-slate-600/40 text-slate-400">
                                  <span className="text-[10px] uppercase opacity-70">SPY</span>
                                  <span className="font-mono font-bold">— self</span>
                                </div>
                              );
                            }
                            const arrow = s.dir === "up" ? "▲" : s.dir === "down" ? "▼" : "—";
                            const cls = s.dir === "up" ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                                      : s.dir === "down" ? "bg-red-500/10 border-red-500/30 text-red-300"
                                      : "bg-slate-700/30 border-slate-600/40 text-slate-400";
                            return (
                              <div className={`flex flex-col items-center justify-center rounded px-1.5 py-1 border ${cls}`}>
                                <span className="text-[10px] uppercase opacity-70">SPY</span>
                                <span className="font-mono font-bold">{arrow} {s.pct_from_ema50.toFixed(1)}%</span>
                              </div>
                            );
                          })()}
                          {(() => {
                            const r = judgment.macro_context.regime;
                            if (!r) {
                              return (
                                <div className="flex flex-col items-center justify-center rounded px-1.5 py-1 border bg-slate-700/30 border-slate-600/40 text-slate-400">
                                  <span className="text-[10px] uppercase opacity-70">Regime</span>
                                  <span className="font-mono font-bold">—</span>
                                </div>
                              );
                            }
                            const cls = r.label === "bull" ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                                      : r.label === "bear" ? "bg-red-500/10 border-red-500/30 text-red-300"
                                      : r.label === "risk-off" ? "bg-orange-500/10 border-orange-500/30 text-orange-300"
                                      : "bg-slate-700/30 border-slate-600/40 text-slate-400";
                            return (
                              <div className={`flex flex-col items-center justify-center rounded px-1.5 py-1 border ${cls}`} title={`VIX ${r.vix}`}>
                                <span className="text-[10px] uppercase opacity-70">Regime</span>
                                <span className="font-mono font-bold uppercase">{r.label}</span>
                              </div>
                            );
                          })()}
                        </div>
                      </div>
                    )}
                    {judgment.evidence_pillars && (
                      <div className="bg-slate-800/50 rounded-lg p-2.5 space-y-1.5">
                        <div className="flex justify-between items-center text-xs">
                          <span className="text-slate-500 font-semibold">EVIDENCE PILLARS</span>
                          <span className={`font-mono font-bold ${
                            judgment.evidence_pillars.aligned >= 3 ? "text-emerald-400" :
                            judgment.evidence_pillars.aligned === 2 ? "text-amber-400" :
                            "text-red-400"
                          }`}>
                            {judgment.evidence_pillars.aligned}/{judgment.evidence_pillars.total}
                          </span>
                        </div>
                        <div className="grid grid-cols-4 gap-1.5 text-[11px]">
                          {(["trend", "momentum", "volume", "price"] as const).map((p) => {
                            const ok = judgment.evidence_pillars?.[p];
                            return (
                              <div key={p} className={`flex items-center justify-center gap-1 rounded px-1.5 py-1 border ${
                                ok ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                                   : "bg-slate-700/30 border-slate-600/40 text-slate-500"
                              }`}>
                                <span>{ok ? "✓" : "✗"}</span>
                                <span className="capitalize">{p}</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                    <p className="text-xs text-slate-500 bg-slate-800/50 rounded-lg p-2">{judgment.judge_reason}</p>
                    {/* Probability of hitting target — full indicator breakdown */}
                    <TargetHitBreakdown judgment={judgment} />
                    {/* Buy/sell volume pressure */}
                    {indicators && (indicators as any).up_dn_vol_ratio !== undefined && (
                      <div className="bg-slate-800/50 rounded-lg p-2.5 space-y-1.5">
                        <div className="text-xs text-slate-500 font-semibold">VOLUME PRESSURE (20-day)</div>
                        {(() => {
                          const ratio = (indicators as any).up_dn_vol_ratio as number;
                          const buyPct = Math.round((ratio / (ratio + 1)) * 100);
                          const sellPct = 100 - buyPct;
                          return (
                            <>
                              <div className="flex h-2 rounded-full overflow-hidden">
                                <div className="bg-emerald-500 transition-all" style={{ width: `${buyPct}%` }} />
                                <div className="bg-red-500 transition-all" style={{ width: `${sellPct}%` }} />
                              </div>
                              <div className="flex justify-between text-xs">
                                <span className="text-emerald-400 font-semibold">▲ Buy {buyPct}%</span>
                                <span className="text-red-400 font-semibold">▼ Sell {sellPct}%</span>
                              </div>
                            </>
                          );
                        })()}
                      </div>
                    )}
                    {judgment.fear_greed_label && (
                      <div className="text-xs text-center text-slate-500">
                        Market Sentiment: <span className={judgment.fear_greed_score! >= 55 ? "text-emerald-400" : judgment.fear_greed_score! <= 40 ? "text-red-400" : "text-amber-400"}>{judgment.fear_greed_label} ({judgment.fear_greed_score})</span>
                      </div>
                    )}
                  </>
                ) : null}
              </div>
            )}

            {/* ── AGENTS TAB ── */}
            {tab === "agents" && (
              <div className="space-y-2">
                {votes.length === 0 && <p className="text-slate-500 text-sm text-center py-6">Run analysis to see agent votes</p>}
                {votes.map((v, i) => {
                  const st = signalStyle(v.vote);
                  return (
                    <div key={i} className={`rounded-lg p-2.5 border ${st.bg} ${st.border}`}>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-semibold">{v.emoji} {v.agent}</span>
                        <SignalBadge signal={v.vote} size="sm" />
                      </div>
                      <div className="text-xs text-slate-400 mb-1.5 leading-relaxed">{v.reason}</div>
                      {v.method && <div className="text-xs text-slate-600 italic border-t border-slate-700 pt-1">{v.method}</div>}
                      <div className="flex items-center gap-2 mt-1.5">
                        <div className="flex-1">
                          <AccBar pct={v.confidence} color={st.hex} />
                        </div>
                        <span className={`text-xs font-bold ${st.text}`}>{v.confidence.toFixed(0)}%</span>
                        {v.weight && v.weight !== 1 && <span className="text-xs text-slate-600">w:{v.weight.toFixed(2)}</span>}
                      </div>
                    </div>
                  );
                })}
                {analyzing && votes.length < 9 && (
                  <div className="rounded-lg p-3 border border-slate-700 bg-slate-800/30 text-center text-xs text-slate-500 animate-pulse">
                    Waiting for {9 - votes.length} more agent{9 - votes.length > 1 ? "s" : ""}...
                  </div>
                )}
              </div>
            )}

            {/* ── OPTIONS TAB ── */}
            {tab === "options" && (
              <div className="space-y-3">
                {!judgment ? (
                  <p className="text-slate-500 text-sm text-center py-6">Run analysis to see options details</p>
                ) : (
                  <>
                    <div className="text-center mb-2">
                      <SignalBadge signal={judgment.signal} size="md" />
                    </div>
                    <div className="bg-slate-800/60 rounded-xl p-3 space-y-1">
                      <div className="text-xs text-slate-500 font-semibold">STRIKE</div>
                      <div className="text-sm font-bold text-slate-200">{judgment.strike_hint}</div>
                    </div>
                    {/* Expiry date cards */}
                    {judgment.expiry_weekly && (
                      <div>
                        <div className="text-xs text-slate-500 font-semibold mb-1.5">EXPIRY OPTIONS</div>
                        <div className="grid grid-cols-3 gap-1.5">
                          {[
                            { label: "Weekly", date: judgment.expiry_weekly, note: "7 DTE", color: "border-emerald-700 bg-emerald-900/20" },
                            { label: "2-Week", date: judgment.expiry_biweekly, note: "14 DTE", color: "border-blue-700 bg-blue-900/20" },
                            { label: "Monthly", date: judgment.expiry_monthly, note: "3rd Fri", color: "border-purple-700 bg-purple-900/20" },
                          ].map(e => (
                            <div key={e.label} className={`rounded-lg border p-2 text-center ${e.color}`}>
                              <div className="text-xs text-slate-500">{e.label}</div>
                              <div className="text-sm font-bold text-white">{e.date}</div>
                              <div className="text-xs text-slate-500">{e.note}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="bg-slate-800/60 rounded-xl p-3 space-y-1">
                      <div className="text-xs text-slate-500 font-semibold">ENTRY TRIGGER</div>
                      <div className="text-sm text-slate-200 leading-relaxed">{judgment.entry_trigger}</div>
                    </div>
                    <div className="bg-slate-800/60 rounded-xl p-3 space-y-1">
                      <div className="text-xs text-slate-500 font-semibold">RISK NOTE</div>
                      <div className="text-sm text-slate-200 leading-relaxed">{judgment.risk_note}</div>
                    </div>
                    {judgment.post_forecast_note && (
                      <div className={`rounded-xl p-3 space-y-1 border ${
                        judgment.post_forecast_mode === "continuation"
                          ? "bg-emerald-900/20 border-emerald-800/50"
                          : judgment.post_forecast_mode === "reversion"
                          ? "bg-amber-900/20 border-amber-800/50"
                          : "bg-slate-800/60 border-slate-700"
                      }`}>
                        <div className="text-xs text-slate-400 font-semibold flex items-center gap-2">
                          AFTER PREDICTION
                          <span className={`text-[10px] px-2 py-0.5 rounded-full ${
                            judgment.post_forecast_mode === "continuation" ? "bg-emerald-500/30 text-emerald-300" :
                            judgment.post_forecast_mode === "reversion"    ? "bg-amber-500/30 text-amber-300" :
                                                                              "bg-slate-500/30 text-slate-300"
                          }`}>
                            {judgment.post_forecast_mode === "continuation" ? "CONTINUES" :
                             judgment.post_forecast_mode === "reversion"    ? "PULLS BACK" : "DRIFTS"}
                          </span>
                        </div>
                        <div className="text-sm text-slate-200 leading-relaxed">{judgment.post_forecast_note}</div>
                      </div>
                    )}
                    <div className="grid grid-cols-2 gap-2">
                      <div className="bg-emerald-900/30 border border-emerald-800/50 rounded-xl p-3 text-center">
                        <div className="text-xs text-slate-500 mb-1">TARGET</div>
                        <div className="text-base font-bold font-mono text-emerald-400">${judgment.target_price.toFixed(2)}</div>
                      </div>
                      <div className="bg-red-900/30 border border-red-800/50 rounded-xl p-3 text-center">
                        <div className="text-xs text-slate-500 mb-1">STOP</div>
                        <div className="text-base font-bold font-mono text-red-400">${judgment.stop_loss.toFixed(2)}</div>
                      </div>
                    </div>
                    <div className="bg-blue-900/20 border border-blue-800/30 rounded-xl p-3 text-center">
                      <div className="text-xs text-slate-500 mb-1">POSITION SIZE</div>
                      <div className="text-xl font-black text-blue-400">{judgment.position_size_pct}% of account</div>
                    </div>
                    {judgment.track_record && (
                      <div className={`rounded-xl p-3 border ${
                        judgment.track_record.rating === "strong" ? "bg-emerald-900/20 border-emerald-800/40" :
                        judgment.track_record.rating === "good"   ? "bg-sky-900/20 border-sky-800/40" :
                        judgment.track_record.rating === "weak"   ? "bg-amber-900/20 border-amber-800/40" :
                                                                    "bg-red-900/20 border-red-800/40"
                      }`}>
                        <div className="flex items-baseline justify-between mb-1">
                          <div className={`text-xs font-bold tracking-wider ${
                            judgment.track_record.rating === "strong" ? "text-emerald-400" :
                            judgment.track_record.rating === "good"   ? "text-sky-400" :
                            judgment.track_record.rating === "weak"   ? "text-amber-400" :
                                                                        "text-red-400"
                          }`}>MODEL TRACK RECORD</div>
                          <div className="text-2xl font-black font-mono">
                            {judgment.track_record.hit_rate.toFixed(0)}%
                          </div>
                        </div>
                        <div className="text-[11px] text-slate-300">{judgment.track_record.note}</div>
                      </div>
                    )}
                    {judgment.kelly && (
                      <div className="bg-emerald-900/20 border border-emerald-800/40 rounded-xl p-3">
                        <div className="flex items-baseline justify-between mb-1">
                          <div className="text-xs text-emerald-400 font-bold tracking-wider">KELLY (regime-aware)</div>
                          <div className="text-[10px] text-slate-500 uppercase">{judgment.kelly.regime.replace("_", " ")}</div>
                        </div>
                        {judgment.kelly.kelly_pct > 0 ? (
                          <>
                            <div className="text-2xl font-black text-emerald-400 font-mono">
                              {judgment.kelly.kelly_pct.toFixed(1)}%
                              <span className="text-sm text-slate-400 font-normal ml-2">
                                = ${judgment.kelly.dollars_per_10k.toFixed(0)} / $10k
                              </span>
                            </div>
                            <div className="text-[11px] text-slate-400 mt-1">
                              Win prob {(judgment.kelly.win_prob_used * 100).toFixed(0)}% · R:R {judgment.kelly.rr_planned.toFixed(2)} · Half-Kelly safety
                            </div>
                          </>
                        ) : (
                          <div className="text-sm text-slate-400 italic">{judgment.kelly.explanation}</div>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* ── CHAIN TAB ── */}
            {tab === "chain" && (
              <div className="space-y-2">
                {chainLoading && (
                  <div className="text-center py-8 text-slate-500 text-sm animate-pulse">Loading options chain…</div>
                )}
                {!chainLoading && !chain && (
                  <p className="text-slate-500 text-sm text-center py-6">Click CHAIN to load live options data</p>
                )}
                {!chainLoading && chain && (chain as any).error && (
                  <p className="text-red-400 text-sm text-center py-6">{(chain as any).error}</p>
                )}
                {!chainLoading && chain && !(chain as any).error && (
                  <>
                    {/* Header */}
                    <div className="flex items-center justify-between">
                      <div className="text-xs font-bold text-slate-300">{chain.symbol} — ${chain.price.toFixed(2)}</div>
                      <button onClick={() => fetchChain(symbol)} className="text-xs text-blue-400 hover:text-blue-300">↻ Refresh</button>
                    </div>

                    {/* Expiry selector */}
                    <div className="flex flex-wrap gap-1">
                      {chain.available_expiries.map(exp => (
                        <button key={exp} onClick={() => { setChainExpiry(exp); fetchChain(symbol, exp); }}
                          className={`px-2 py-0.5 rounded text-xs font-mono transition-colors ${exp === chainExpiry ? "bg-blue-600 text-white" : "bg-slate-800 text-slate-400 hover:bg-slate-700"}`}>
                          {exp}
                        </button>
                      ))}
                    </div>

                    {/* Calls / Puts toggle */}
                    <div className="flex rounded-lg overflow-hidden border border-slate-700 text-xs font-bold">
                      <button onClick={() => setChainSide("calls")}
                        className={`flex-1 py-1.5 transition-colors ${chainSide === "calls" ? "bg-emerald-700 text-white" : "bg-slate-800 text-slate-400 hover:bg-slate-700"}`}>
                        CALLS ({chain.calls.length})
                      </button>
                      <button onClick={() => setChainSide("puts")}
                        className={`flex-1 py-1.5 transition-colors ${chainSide === "puts" ? "bg-red-700 text-white" : "bg-slate-800 text-slate-400 hover:bg-slate-700"}`}>
                        PUTS ({chain.puts.length})
                      </button>
                    </div>

                    {/* Column headers */}
                    <div className="grid text-xs text-slate-500 font-semibold px-1" style={{ gridTemplateColumns: "3fr 2fr 2fr 2fr 2fr 2fr" }}>
                      <span>STRIKE</span><span className="text-right">MID</span><span className="text-right">IV%</span>
                      <span className="text-right">VOL</span><span className="text-right">OI</span><span className="text-right">Δ</span>
                    </div>

                    {/* Rows */}
                    <div className="space-y-px">
                      {chain[chainSide].map((row) => {
                        const isAtm = Math.abs(row.strike - chain.price) / chain.price < 0.02;
                        const bg = row.itm
                          ? chainSide === "calls" ? "bg-emerald-900/25" : "bg-red-900/25"
                          : "bg-slate-800/40";
                        return (
                          <div key={row.strike}
                            className={`grid items-center rounded px-1.5 py-1 text-xs font-mono ${bg} ${isAtm ? "ring-1 ring-yellow-500/60" : ""}`}
                            style={{ gridTemplateColumns: "3fr 2fr 2fr 2fr 2fr 2fr" }}>
                            <span className={`font-bold ${isAtm ? "text-yellow-400" : row.itm ? (chainSide === "calls" ? "text-emerald-400" : "text-red-400") : "text-slate-300"}`}>
                              ${row.strike.toFixed(0)}{isAtm ? " ◀" : ""}
                            </span>
                            <span className="text-right text-white">${row.mid.toFixed(2)}</span>
                            <span className={`text-right ${row.iv > 60 ? "text-orange-400" : row.iv > 40 ? "text-yellow-400" : "text-slate-400"}`}>{row.iv}%</span>
                            <span className="text-right text-slate-400">{row.volume > 999 ? `${(row.volume / 1000).toFixed(1)}k` : row.volume}</span>
                            <span className="text-right text-slate-400">{row.open_interest > 999 ? `${(row.open_interest / 1000).toFixed(1)}k` : row.open_interest}</span>
                            <span className={`text-right ${chainSide === "calls" ? "text-emerald-400" : "text-red-400"}`}>{row.delta > 0 ? "+" : ""}{row.delta.toFixed(2)}</span>
                          </div>
                        );
                      })}
                    </div>

                    <div className="text-xs text-slate-600 text-center pt-1">
                      ◀ = at-the-money · highlighted = in-the-money
                    </div>
                  </>
                )}
              </div>
            )}

            {/* ── NEWS TAB ── */}
            {tab === "news" && (
              <div className="space-y-2">
                <div className="text-xs text-slate-500 font-semibold uppercase mb-2">Latest News — {symbol}</div>
                {(livePrice?.news || []).length === 0 && (
                  <p className="text-slate-500 text-sm text-center py-4">Run analysis to load news</p>
                )}
                {(livePrice?.news || []).map((n, i) => (
                  <a key={i} href={n.url} target="_blank" rel="noreferrer"
                    className="block bg-slate-800/60 rounded-lg p-2.5 hover:bg-slate-700/60 transition-all">
                    <div className="text-xs font-semibold text-slate-200 leading-snug mb-1">{n.title}</div>
                    {n.summary && <div className="text-xs text-slate-500 line-clamp-2">{n.summary}</div>}
                    <div className="text-xs text-slate-600 mt-1">{n.source}</div>
                  </a>
                ))}
              </div>
            )}

            {/* ── FEAR & GREED TAB ── */}
            {tab === "fear" && (
              <div className="space-y-4">
                <div className="text-xs text-slate-500 font-semibold uppercase">Market Fear & Greed Index</div>
                <FearGreedGauge data={fearGreed} />
                {fearGreed && (
                  <div className="space-y-2">
                    <div className="text-xs text-slate-500 font-semibold uppercase">Market Components</div>
                    {fearGreed.components.map((c, i) => (
                      <div key={i} className="bg-slate-800/60 rounded-lg p-2.5">
                        <div className="flex justify-between items-center mb-1">
                          <span className="text-xs font-semibold text-slate-300">{c.name}</span>
                          <span className={`text-xs font-bold ${c.score >= 60 ? "text-emerald-400" : c.score <= 40 ? "text-red-400" : "text-amber-400"}`}>{c.score.toFixed(0)}/100</span>
                        </div>
                        <AccBar pct={c.score} color={c.score >= 60 ? "#10b981" : c.score <= 40 ? "#ef4444" : "#f59e0b"} />
                        <div className="text-xs text-slate-500 mt-1">{c.label}</div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Per-stock sentiment */}
                {stockSentiment && (
                  <div className="space-y-2 pt-2 border-t border-slate-800">
                    <div className="flex items-center justify-between">
                      <div className="text-xs text-slate-500 font-semibold uppercase">{stockSentiment.symbol} Sentiment</div>
                      <span className="text-xs font-bold" style={{ color: stockSentiment.color }}>{stockSentiment.score} — {stockSentiment.label}</span>
                    </div>
                    {stockSentiment.components.map((c, i) => (
                      <div key={i} className="bg-slate-800/60 rounded-lg p-2.5">
                        <div className="flex justify-between items-center mb-1">
                          <span className="text-xs font-semibold text-slate-300">{c.name}</span>
                          <span className={`text-xs font-bold ${c.score >= 60 ? "text-emerald-400" : c.score <= 40 ? "text-red-400" : "text-amber-400"}`}>{c.score.toFixed(0)}/100</span>
                        </div>
                        <AccBar pct={c.score} color={c.score >= 60 ? "#10b981" : c.score <= 40 ? "#ef4444" : "#f59e0b"} />
                        <div className="text-xs text-slate-500 mt-1">{c.label}</div>
                      </div>
                    ))}
                    <button onClick={() => loadStockSentiment(symbol)} className="w-full text-xs text-blue-400 hover:text-blue-300 py-1">
                      ↻ Refresh {symbol} sentiment
                    </button>
                  </div>
                )}

                <div className="text-xs text-slate-500 font-semibold uppercase mt-4">Trump / Macro News</div>
                {fearLoading && <p className="text-xs text-slate-500 animate-pulse">Refreshing market data…</p>}
                {politicalNews.length === 0 && !fearLoading && (
                  <p className="text-xs text-slate-500">No political news loaded yet.</p>
                )}
                {politicalNews.map((n, i) => (
                  <a key={i} href={n.url} target="_blank" rel="noreferrer"
                    className="block bg-slate-800/50 rounded-lg p-2.5 hover:bg-slate-700/50 transition-all border-l-2 border-amber-600/50">
                    <div className="text-xs font-semibold text-slate-200 leading-snug">{n.title}</div>
                    <div className="text-xs text-slate-600 mt-1">{n.source} · {n.published_at?.slice(0, 16)}</div>
                  </a>
                ))}
                <button onClick={() => { loadFearGreed(true); loadStockSentiment(symbol); }} className="w-full mt-2 text-xs text-blue-400 hover:text-blue-300 py-1">
                  ↻ Force refresh Fear & Greed + News
                </button>
              </div>
            )}

            {/* ── ACCURACY TAB ── */}
            {tab === "accuracy" && (
              <div className="space-y-3">
                {/* Learning persistence panel — shows the user that the AI is
                    accumulating data and that backups are protecting their
                    learning against accidental file resets. */}
                {learning && (
                  <div className={`rounded-xl p-3 border space-y-2 ${
                    learning.is_learning
                      ? "bg-emerald-900/20 border-emerald-800/50"
                      : "bg-slate-800/50 border-slate-700"
                  }`}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`inline-block w-2 h-2 rounded-full ${
                          learning.is_learning ? "bg-emerald-400 animate-pulse" : "bg-slate-500"
                        }`} />
                        <span className="text-xs font-bold text-slate-200 uppercase tracking-wide">
                          AI Learning {learning.is_learning ? "Active" : "Idle"}
                        </span>
                      </div>
                      <button
                        onClick={triggerBackup}
                        disabled={backupBusy}
                        className="text-[10px] px-2 py-0.5 rounded bg-blue-600/30 hover:bg-blue-600/50 text-blue-300 border border-blue-700/50 disabled:opacity-50">
                        {backupBusy ? "Saving…" : "Back up now"}
                      </button>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div className="bg-slate-900/40 rounded p-1.5">
                        <div className="text-[10px] text-slate-500 uppercase">Logged</div>
                        <div className="text-base font-bold text-slate-100">{learning.predictions_total ?? 0}</div>
                      </div>
                      <div className="bg-slate-900/40 rounded p-1.5">
                        <div className="text-[10px] text-slate-500 uppercase">Resolved</div>
                        <div className="text-base font-bold text-emerald-400">{learning.predictions_resolved ?? 0}</div>
                      </div>
                      <div className="bg-slate-900/40 rounded p-1.5">
                        <div className="text-[10px] text-slate-500 uppercase">Pending</div>
                        <div className="text-base font-bold text-amber-400">{learning.predictions_pending ?? 0}</div>
                      </div>
                    </div>
                    <div className="text-[10px] text-slate-500 leading-relaxed">
                      <div>
                        DB <span className="text-slate-300 font-mono">predictions.db</span> ({((learning.db_size_bytes ?? 0) / 1024).toFixed(0)} KB)
                        {learning.weights_adjusting
                          ? <span className="text-emerald-400"> · weights adjusting</span>
                          : <span className="text-slate-500"> · weights frozen until first resolution</span>}
                      </div>
                      <div className="mt-0.5">
                        Backups: <span className="text-slate-300">{learning.backup_count ?? 0}</span>
                        {learning.latest_backup && (
                          <span> · last <span className="text-slate-300 font-mono">{learning.latest_backup}</span></span>
                        )}
                      </div>
                      {learning.latest_backup_iso && (
                        <div className="mt-0.5 text-slate-600">
                          Last snapshot: {new Date(learning.latest_backup_iso).toLocaleString()}
                        </div>
                      )}
                    </div>
                    {!learning.weights_adjusting && (learning.predictions_total ?? 0) > 0 && (
                      <div className="text-[10px] text-amber-400/80 bg-amber-900/20 border border-amber-800/40 rounded p-1.5">
                        Predictions are logged but none have hit their hold window yet.
                        Check back after market close for swing/position trades, or within
                        2-6 hours for intraday/day signals.
                      </div>
                    )}
                  </div>
                )}

                {/* ── Live "AI is learning" feed ─────────────────────────────
                    Each row is one resolved vote with the weight value
                    BEFORE and AFTER, so you literally watch the agents move
                    on the leaderboard as outcomes roll in. */}
                <div className="rounded-xl bg-slate-800/50 border border-slate-700 p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="text-xs font-bold text-slate-200 uppercase tracking-wide">
                      Learning Feed
                    </div>
                    <span className="text-[10px] text-slate-500">
                      last {Math.min(learningEvents.length, 30)} weight changes
                    </span>
                  </div>
                  {learningEvents.length === 0 ? (
                    <div className="text-[11px] text-slate-500 italic py-2 text-center">
                      No resolved votes yet — feed populates as predictions hit
                      their hold window and get graded.
                    </div>
                  ) : (
                    <div className="max-h-72 overflow-y-auto space-y-1 pr-1">
                      {learningEvents.map((ev) => {
                        const isWin = ev.was_correct === 1;
                        const isCall = ev.vote === "BUY_CALL";
                        const isPut = ev.vote === "BUY_PUT";
                        const voteLabel = isCall ? "CALL" : isPut ? "PUT" : "HOLD";
                        const voteColor = isCall ? "text-emerald-400"
                                          : isPut ? "text-red-400"
                                          : "text-slate-400";
                        const moved = ev.delta !== 0;
                        const arrow = ev.delta > 0 ? "▲" : ev.delta < 0 ? "▼" : "·";
                        const deltaColor = ev.delta > 0 ? "text-emerald-400"
                                           : ev.delta < 0 ? "text-red-400"
                                           : "text-slate-500";
                        // Compact relative time (fits in the row)
                        let rel = "";
                        try {
                          const ts = new Date(ev.created_at).getTime();
                          const mins = Math.max(0, Math.round((Date.now() - ts) / 60000));
                          rel = mins < 60 ? `${mins}m ago`
                              : mins < 1440 ? `${Math.round(mins / 60)}h ago`
                              : `${Math.round(mins / 1440)}d ago`;
                        } catch {}
                        return (
                          <div key={ev.id} className="flex items-center gap-2 text-[11px] bg-slate-900/40 rounded px-2 py-1">
                            <span className={`font-bold ${isWin ? "text-emerald-400" : "text-red-400"} w-7 flex-shrink-0`}>
                              {isWin ? "WIN" : "LOSS"}
                            </span>
                            <span className="text-slate-200 truncate flex-1 min-w-0">
                              {ev.agent} · <span className="text-slate-500">{ev.symbol}</span>
                              {" "}<span className={voteColor}>{voteLabel}</span>
                            </span>
                            <span className="font-mono text-slate-500 text-[10px] hidden sm:inline">
                              {ev.weight_before.toFixed(2)}→{ev.weight_after.toFixed(2)}
                            </span>
                            <span className={`font-mono font-bold ${deltaColor} w-14 text-right flex-shrink-0`}>
                              {moved ? `${arrow} ${ev.delta > 0 ? "+" : ""}${ev.delta.toFixed(3)}`
                                     : ev.phase === "warmup" ? "warmup" : "·"}
                            </span>
                            <span className="text-slate-600 text-[10px] w-14 text-right flex-shrink-0">
                              {rel}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                <div className="text-xs text-slate-500 font-semibold uppercase">AI Agent Accuracy Log</div>
                {accuracy ? (
                  <>
                    {/* Overall */}
                    <div className="bg-blue-900/30 border border-blue-800/50 rounded-xl p-3 text-center">
                      <div className="text-xs text-slate-500 mb-1">OVERALL WIN RATE</div>
                      <div className="text-2xl font-black text-blue-400">
                        {((accuracy.overall_win_rate ?? 0) * 100).toFixed(1)}%
                      </div>
                      <div className="text-xs text-slate-500 mt-1">
                        {accuracy.correct_predictions ?? 0} correct / {accuracy.total_predictions ?? 0} total predictions
                      </div>
                    </div>

                    {/* Per-agent */}
                    {(accuracy.agents || []).map((a, i) => {
                      const wr = (a.win_rate ?? 0) * 100;
                      const color = wr >= 65 ? "#10b981" : wr >= 50 ? "#f59e0b" : "#ef4444";
                      return (
                        <div key={i} className="bg-slate-800/50 rounded-lg p-2.5 space-y-2">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-bold text-slate-200">{a.agent}</span>
                            <span className="text-xs font-bold" style={{ color }}>{wr.toFixed(0)}%</span>
                          </div>
                          <AccBar pct={wr} color={color} />
                          <div className="flex gap-2 text-xs text-slate-500">
                            <span>CALL {a.call_correct}/{a.call_total}</span>
                            <span>PUT {a.put_correct}/{a.put_total}</span>
                            <span>Total {a.total}</span>
                          </div>
                        </div>
                      );
                    })}

                    {(accuracy.agents || []).length === 0 && (
                      <div className="text-center py-4">
                        <p className="text-slate-500 text-xs">No prediction outcomes verified yet.</p>
                        <p className="text-slate-600 text-xs mt-1">Run analyses and check back in 1-2 days as predictions resolve.</p>
                      </div>
                    )}
                    <button onClick={loadAccuracy} className="w-full text-xs text-blue-400 hover:text-blue-300 py-1">
                      ↻ Refresh Accuracy
                    </button>
                  </>
                ) : (
                  <div className="text-center py-6">
                    <p className="text-slate-500 text-sm">Loading accuracy data...</p>
                  </div>
                )}
              </div>
            )}

            {/* ── PAPER TRADING TAB ── */}
            {tab === "paper" && (
              <div className="space-y-3">
                {/* Account Header */}
                {paperAccount && (() => {
                  const a = paperAccount;
                  const ret = a.total_return_pct ?? 0;
                  const retCls = ret > 0 ? "text-emerald-400" : ret < 0 ? "text-red-400" : "text-slate-300";
                  const unr = a.unrealized_pnl ?? 0;
                  const unrCls = unr > 0 ? "text-emerald-400" : unr < 0 ? "text-red-400" : "text-slate-400";
                  return (
                    <div className="rounded-xl border border-blue-500/30 bg-gradient-to-br from-blue-900/30 to-slate-900/40 p-3 space-y-2">
                      <div className="flex items-center justify-between">
                        <div className="text-xs text-slate-400 uppercase tracking-wide">Paper Account</div>
                        <button onClick={resetPaperAccount}
                          className="text-[10px] text-slate-500 hover:text-red-400 px-2 py-0.5 border border-slate-700 hover:border-red-500/50 rounded">
                          Reset
                        </button>
                      </div>
                      <div className="flex items-baseline justify-between">
                        <div>
                          <div className="text-[10px] text-slate-500 uppercase">Equity</div>
                          <div className="text-2xl font-bold font-mono">${(a.equity ?? 0).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}</div>
                        </div>
                        <div className={`text-right ${retCls}`}>
                          <div className="text-[10px] uppercase opacity-70">Return</div>
                          <div className="text-lg font-bold font-mono">{ret >= 0 ? "+" : ""}{ret.toFixed(2)}%</div>
                        </div>
                      </div>
                      <div className="grid grid-cols-3 gap-1.5 text-[11px] pt-1 border-t border-slate-700/50">
                        <div>
                          <div className="text-slate-500">Cash</div>
                          <div className="font-mono font-semibold">${(a.cash ?? 0).toFixed(0)}</div>
                        </div>
                        <div>
                          <div className="text-slate-500">Held</div>
                          <div className="font-mono font-semibold">${(a.held_value ?? 0).toFixed(0)}</div>
                        </div>
                        <div>
                          <div className="text-slate-500">Unrealized</div>
                          <div className={`font-mono font-semibold ${unrCls}`}>{unr >= 0 ? "+" : ""}${unr.toFixed(2)}</div>
                        </div>
                      </div>
                    </div>
                  );
                })()}

                {/* Quick-trade card */}
                {judgment && judgment.signal !== "HOLD" && (
                  <div className="rounded-xl border border-emerald-500/30 bg-emerald-900/10 p-2.5 flex items-center justify-between gap-2">
                    <div className="text-xs">
                      <div className="text-slate-400">Current Signal</div>
                      <div className="font-bold">
                        <span className={judgment.signal === "BUY_CALL" ? "text-emerald-400" : "text-red-400"}>
                          {judgment.signal === "BUY_CALL" ? "CALL" : "PUT"}
                        </span>
                        <span className="text-slate-300"> {symbol} @ ${judgment.entry_price.toFixed(2)}</span>
                      </div>
                    </div>
                    <button onClick={tradeCurrentSignal} disabled={paperLoading}
                      className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded text-xs font-bold">
                      Trade Now
                    </button>
                  </div>
                )}

                {/* Open Positions */}
                <div>
                  <div className="text-xs text-slate-500 font-semibold mb-1.5 flex items-center justify-between">
                    <span>OPEN POSITIONS ({paperOpen.length})</span>
                    <button onClick={loadPaperData} className="text-blue-400 hover:text-blue-300 font-normal">↻</button>
                  </div>
                  {paperOpen.length === 0 ? (
                    <div className="text-center py-4 text-slate-500 text-xs border border-dashed border-slate-700 rounded-lg">
                      No open positions. Analyze a stock and click Trade Now.
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {paperOpen.map((p) => {
                        const pnl = p.unrealized_pnl ?? 0;
                        const pnlPct = p.unrealized_pnl_pct ?? 0;
                        const pnlCls = pnl > 0 ? "text-emerald-400" : pnl < 0 ? "text-red-400" : "text-slate-400";
                        const sigCls = p.signal === "BUY_CALL" ? "bg-emerald-500/20 text-emerald-300" : "bg-red-500/20 text-red-300";
                        return (
                          <div key={p.id} className="rounded-lg border border-slate-700 bg-slate-800/40 p-2 text-xs space-y-1.5">
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-1.5">
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${sigCls}`}>
                                  {p.signal === "BUY_CALL" ? "CALL" : "PUT"}
                                </span>
                                <span className="font-bold">{p.symbol}</span>
                                <span className="text-slate-500 text-[10px]">{p.horizon}</span>
                              </div>
                              <button onClick={() => closePaperPosition(p.id)} disabled={paperLoading}
                                className="text-[10px] px-1.5 py-0.5 border border-slate-600 hover:border-red-400 hover:text-red-400 rounded">
                                Close
                              </button>
                            </div>
                            <div className="grid grid-cols-3 gap-1 text-[10px] text-slate-400">
                              <div>Entry <span className="text-slate-200 font-mono">${p.entry_price.toFixed(2)}</span></div>
                              <div>Now <span className="text-slate-200 font-mono">{p.current_price ? `$${p.current_price.toFixed(2)}` : "—"}</span></div>
                              <div>Cost <span className="text-slate-200 font-mono">${p.cost.toFixed(0)}</span></div>
                              <div>Target <span className="text-emerald-300 font-mono">${p.target_price.toFixed(2)}</span></div>
                              <div>Stop <span className="text-red-300 font-mono">${p.stop_loss.toFixed(2)}</span></div>
                              <div>Conf <span className="text-slate-200 font-mono">{p.confidence?.toFixed(0) ?? "—"}%</span></div>
                            </div>
                            <div className="flex items-center justify-between border-t border-slate-700 pt-1">
                              <span className="text-slate-500 text-[10px]">Unrealized P/L</span>
                              <span className={`font-mono font-bold ${pnlCls}`}>
                                {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)} ({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%)
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Closed History */}
                {paperHistory.length > 0 && (
                  <div>
                    <div className="text-xs text-slate-500 font-semibold mb-1.5">
                      HISTORY ({paperHistory.length}) · Win Rate {paperAccount?.stats?.win_rate ?? 0}%
                    </div>
                    <div className="space-y-1">
                      {paperHistory.slice(0, 12).map((p) => {
                        const pnl = p.pnl ?? 0;
                        const pnlCls = pnl > 0 ? "text-emerald-400" : pnl < 0 ? "text-red-400" : "text-slate-400";
                        const reason = p.close_reason || "—";
                        const reasonCls = reason === "target" ? "text-emerald-400"
                                        : reason === "stop"   ? "text-red-400"
                                        : "text-slate-400";
                        return (
                          <div key={p.id} className="flex items-center justify-between text-[11px] px-2 py-1 border-b border-slate-800/60">
                            <div className="flex items-center gap-1.5">
                              <span className={p.signal === "BUY_CALL" ? "text-emerald-400" : "text-red-400"}>
                                {p.signal === "BUY_CALL" ? "▲" : "▼"}
                              </span>
                              <span className="font-bold w-12">{p.symbol}</span>
                              <span className={`${reasonCls} text-[10px]`}>{reason}</span>
                            </div>
                            <span className={`font-mono font-bold ${pnlCls}`}>
                              {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Toast */}
                {paperToast && (
                  <div className="fixed bottom-4 right-4 bg-slate-800 border border-blue-500/50 text-blue-300 px-3 py-2 rounded-lg text-sm shadow-lg z-50">
                    {paperToast}
                  </div>
                )}
              </div>
            )}

          </div>

          {/* ── Bottom indicator strip ── */}
          {indicators && Object.keys(indicators).length > 0 && (
            <div className="border-t border-slate-800 px-3 py-2 shrink-0 grid grid-cols-3 gap-1.5">
              {[
                { label: "RSI", val: (indicators.rsi14 as number)?.toFixed(0), color: (indicators.rsi14 as number) > 70 ? "text-red-400" : (indicators.rsi14 as number) < 30 ? "text-emerald-400" : "text-slate-300" },
                { label: "ADX", val: (indicators.adx as number)?.toFixed(0), color: (indicators.adx as number) > 25 ? "text-blue-400" : "text-slate-500" },
                { label: "MACD", val: (indicators.macd_hist as number) != null ? ((indicators.macd_hist as number) >= 0 ? "▲" : "▼") : "--", color: (indicators.macd_hist as number) >= 0 ? "text-emerald-400" : "text-red-400" },
              ].map(s => (
                <div key={s.label} className="bg-slate-800/60 rounded px-1.5 py-1 text-center">
                  <div className={`text-xs font-bold ${s.color}`}>{s.val ?? "--"}</div>
                  <div className="text-xs text-slate-600">{s.label}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
