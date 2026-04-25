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
interface Judgment {
  signal: Signal; confidence: number;
  entry_price: number; stop_loss: number; target_price: number;
  agreed_agents: string[]; disagreed_agents: string[];
  vote_tally: { BUY_CALL: number; BUY_PUT: number; HOLD: number };
  position_size_pct: number; judge_reason: string;
  action: string; strike_hint: string; expiry_hint: string;
  entry_trigger: string; risk_note: string;
  forecast_line?: { time: number; value: number }[];
  fear_greed_score?: number; fear_greed_label?: string;
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
interface FearGreedComp { name: string; score: number; value: number; label: string; weight: number; }
interface FearGreed { score: number; label: string; color: string; components: FearGreedComp[]; }
interface AccuracyAgent {
  agent: string; total: number; correct: number;
  win_rate: number; call_correct: number; put_correct: number;
  call_total: number; put_total: number; weight: number;
}
interface AccuracyReport {
  overall_win_rate?: number; total_predictions?: number;
  correct_predictions?: number; agents?: AccuracyAgent[];
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

// ─── Confidence Ring ──────────────────────────────────────────────────────
function ConfRing({ pct, signal }: { pct: number; signal: Signal }) {
  const r = 38; const circ = 2 * Math.PI * r;
  const st = signalStyle(signal);
  return (
    <svg width="96" height="96" viewBox="0 0 96 96">
      <circle cx="48" cy="48" r={r} stroke="#1e293b" strokeWidth="7" fill="none" />
      <circle cx="48" cy="48" r={r} stroke={st.hex} strokeWidth="7" fill="none"
        strokeDasharray={circ} strokeDashoffset={circ * (1 - pct / 100)}
        strokeLinecap="round" transform="rotate(-90 48 48)" />
      <text x="48" y="53" textAnchor="middle" fill={st.hex} fontSize="17" fontWeight="bold">{pct.toFixed(0)}%</text>
    </svg>
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
        <text x="70" y="75" textAnchor="middle" fill={color} fontSize="16" fontWeight="bold">{score}</text>
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
  const [tab, setTab] = useState<"signal" | "agents" | "options" | "news" | "fear" | "accuracy">("signal");
  const [period, setPeriod] = useState("3mo");
  const [indicators, setIndicators] = useState<Record<string, unknown>>({});
  const [politicalNews, setPoliticalNews] = useState<NewsItem[]>([]);
  const [fearGreed, setFearGreed] = useState<FearGreed | null>(null);
  const [accuracy, setAccuracy] = useState<AccuracyReport | null>(null);
  const [fearLoading, setFearLoading] = useState(false);
  const [indicators_visible, setIndicatorsVisible] = useState({ ema: true, bb: false, vwap: true, st: false });

  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<any>(null);
  const forecastRef = useRef<any>(null);
  const targetLineRef = useRef<any>(null);
  const stopLineRef = useRef<any>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // ── Chart setup ─────────────────────────────────────────────────────────
  const loadChart = useCallback(async (sym: string, p: string) => {
    if (!chartContainerRef.current) return;
    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
      candleRef.current = null;
      forecastRef.current = null;
      targetLineRef.current = null;
      stopLineRef.current = null;
    }
    const container = chartContainerRef.current;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight || 350,
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#94a3b8" },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#334155", scaleMarginTop: 0.1, scaleMarginBottom: 0.2 },
      timeScale: { borderColor: "#334155", timeVisible: true },
    });
    chartRef.current = chart;

    const url = `${API_BASE}/chart/${sym}?period=${p}&interval=${p === "1d" ? "5m" : p === "5d" ? "15m" : "1d"}`;
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

      // SuperTrend
      if (indicators_visible.st && data.supertrend?.length) {
        const stLine = chart.addSeries(LineSeries, { color: "#a855f7", lineWidth: 2, title: "ST" });
        stLine.setData(data.supertrend.filter((d: any) => d.value != null).map((d: any) => ({ time: d.time, value: d.value })));
      }

      chart.timeScale().fitContent();
    } catch (e) {
      console.error("Chart load error", e);
    }
  }, [indicators_visible]);

  const drawPrediction = useCallback((j: Judgment) => {
    if (!chartRef.current || !j.forecast_line?.length) return;
    const st = signalStyle(j.signal);

    // Remove old forecast
    if (forecastRef.current) { try { chartRef.current.removeSeries(forecastRef.current); } catch {} forecastRef.current = null; }
    if (targetLineRef.current) { try { chartRef.current.removeSeries(targetLineRef.current); } catch {} targetLineRef.current = null; }
    if (stopLineRef.current) { try { chartRef.current.removeSeries(stopLineRef.current); } catch {} stopLineRef.current = null; }

    if (j.signal === "HOLD") return;

    const forecast = chartRef.current.addSeries(LineSeries, {
      color: st.hex, lineWidth: 2, lineStyle: 3,
      crosshairMarkerVisible: true,
    });
    forecast.setData(j.forecast_line.map(p => ({ time: p.time as UTCTimestamp, value: p.value })));
    forecastRef.current = forecast;

    if (j.target_price) {
      const tgt = chartRef.current.addSeries(LineSeries, { color: "#10b981", lineWidth: 1, lineStyle: 1 });
      const ts0 = j.forecast_line[0].time as UTCTimestamp;
      const ts1 = j.forecast_line[j.forecast_line.length - 1].time as UTCTimestamp;
      tgt.setData([{ time: ts0, value: j.target_price }, { time: ts1, value: j.target_price }]);
      createSeriesMarkers(tgt, [{ time: ts1, position: "aboveBar", color: "#10b981", shape: "arrowUp", text: `TARGET $${j.target_price}` }]);
      targetLineRef.current = tgt;
    }
    if (j.stop_loss) {
      const stp = chartRef.current.addSeries(LineSeries, { color: "#ef4444", lineWidth: 1, lineStyle: 1 });
      const ts0 = j.forecast_line[0].time as UTCTimestamp;
      const ts1 = j.forecast_line[j.forecast_line.length - 1].time as UTCTimestamp;
      stp.setData([{ time: ts0, value: j.stop_loss }, { time: ts1, value: j.stop_loss }]);
      createSeriesMarkers(stp, [{ time: ts1, position: "belowBar", color: "#ef4444", shape: "arrowDown", text: `STOP $${j.stop_loss}` }]);
      stopLineRef.current = stp;
    }
    chartRef.current.timeScale().scrollToRealTime();
  }, []);

  // ── Initial chart load ─────────────────────────────────────────────────
  useEffect(() => {
    loadChart(symbol, period);
    const resize = () => {
      if (chartRef.current && chartContainerRef.current)
        chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
    };
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [symbol, period]);

  // ── Redraw prediction when judgment arrives ───────────────────────────
  useEffect(() => {
    if (judgment && chartRef.current)
      loadChart(symbol, period).then(() => drawPrediction(judgment));
  }, [judgment]);

  // ── Load Fear & Greed and political news ──────────────────────────────
  const loadFearGreed = useCallback(async () => {
    setFearLoading(true);
    try {
      const [fgRes, polRes] = await Promise.all([
        fetch(`${API_BASE}/fear-greed`),
        fetch(`${API_BASE}/political-news`),
      ]);
      if (fgRes.ok) { const d = await fgRes.json(); if (!d.error) setFearGreed(d); }
      if (polRes.ok) { const d = await polRes.json(); setPoliticalNews(d.news || []); }
    } catch {}
    setFearLoading(false);
  }, []);

  // ── Load accuracy report ───────────────────────────────────────────────
  const loadAccuracy = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/accuracy`);
      if (res.ok) { const d = await res.json(); if (d.report) setAccuracy(d.report); }
    } catch {}
  }, []);

  useEffect(() => {
    loadFearGreed();
    loadAccuracy();
    const iv = setInterval(() => { loadFearGreed(); loadAccuracy(); }, 120000);
    return () => clearInterval(iv);
  }, []);

  // ── WebSocket analysis ────────────────────────────────────────────────
  const runAnalysis = useCallback((sym: string) => {
    if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
    setVotes([]);
    setJudgment(null);
    setStatus("");
    setAnalyzing(true);

    const ws = new WebSocket(`${WS_BASE}/api/ws/analyze/${sym}`);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "live_price") setLivePrice(msg);
      else if (msg.type === "status") setStatus(msg.message);
      else if (msg.type === "agent_vote") setVotes(prev => [...prev, msg.vote]);
      else if (msg.type === "judgment") {
        setJudgment(msg.judgment);
        if (msg.accuracy) setAccuracy(msg.accuracy);
        setAnalyzing(false);
        setStatus("");
        setTab("signal");
      } else if (msg.type === "error") {
        setStatus(`❌ ${msg.message}`);
        setAnalyzing(false);
      }
    };
    ws.onerror = () => { setStatus("❌ WebSocket error"); setAnalyzing(false); };
    ws.onclose = () => { if (analyzing) setAnalyzing(false); };
  }, []);

  const handleAnalyze = () => {
    const s = inputSym.trim().toUpperCase();
    if (!s) return;
    setSymbol(s);
    runAnalysis(s);
  };

  const WATCHLIST = ["AAPL", "NVDA", "TSLA", "MSFT", "SPY", "QQQ", "AMZN", "META", "AMD", "COIN"];
  const TAB_CLASSES = (t: string) =>
    `px-3 py-1.5 text-xs font-semibold rounded-md transition-all cursor-pointer ${tab === t ? "bg-blue-600 text-white" : "text-slate-400 hover:text-white hover:bg-slate-700"}`;

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
        <input
          className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5 text-sm font-mono w-28 focus:outline-none focus:border-blue-500"
          value={inputSym} onChange={e => setInputSym(e.target.value.toUpperCase())}
          onKeyDown={e => e.key === "Enter" && handleAnalyze()}
          placeholder="AAPL"
        />
        <button
          onClick={handleAnalyze}
          disabled={analyzing}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-4 py-1.5 rounded-lg text-sm font-bold transition-all"
        >
          {analyzing ? "Analyzing..." : "Analyze"}
        </button>

        <div className="flex-1 flex flex-wrap gap-1.5 justify-center">
          {WATCHLIST.map(s => (
            <button key={s}
              onClick={() => { setInputSym(s); setSymbol(s); runAnalysis(s); }}
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

      {/* ── Main Area ── */}
      <div className="flex flex-1 overflow-hidden">
        {/* ── LEFT: Chart ── */}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          {/* Chart controls */}
          <div className="flex items-center gap-2 px-3 py-1.5 border-b border-slate-800 shrink-0 bg-[#0d1524]">
            {(["1D","5D","1M","3M","6M"] as const).map(p => {
              const pMap: Record<string, string> = { "1D":"1d","5D":"5d","1M":"1mo","3M":"3mo","6M":"6mo" };
              return (
                <button key={p} onClick={() => setPeriod(pMap[p])}
                  className={`text-xs px-2.5 py-1 rounded font-semibold ${period === pMap[p] ? "bg-blue-600 text-white" : "text-slate-500 hover:text-white"}`}
                >{p}</button>
              );
            })}
            <div className="w-px h-4 bg-slate-700 mx-1" />
            {(["ema","bb","vwap","st"] as const).map(ind => (
              <button key={ind} onClick={() => setIndicatorsVisible(v => ({ ...v, [ind]: !v[ind] }))}
                className={`text-xs px-2.5 py-1 rounded font-semibold ${indicators_visible[ind] ? "bg-slate-600 text-white" : "text-slate-600 hover:text-slate-400"}`}
              >{ind.toUpperCase()}</button>
            ))}
          </div>

          {/* Chart */}
          <div ref={chartContainerRef} className="flex-1 min-h-0" />

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
        <div className="w-72 flex flex-col border-l border-slate-700/50 bg-[#0d1524] shrink-0 overflow-hidden">
          {/* Fear & Greed mini strip */}
          {fearGreed && (
            <div className="px-3 py-2 border-b border-slate-800 shrink-0">
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-slate-500 font-semibold">FEAR & GREED INDEX</span>
                <span className="font-bold" style={{ color: fearGreed.color }}>{fearGreed.score} — {fearGreed.label}</span>
              </div>
              <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden">
                <div className="h-full rounded-full transition-all" style={{ width: `${fearGreed.score}%`, background: `linear-gradient(90deg, #ef4444, #f97316, #f59e0b, #22c55e, #10b981)` }} />
              </div>
            </div>
          )}

          {/* Tabs */}
          <div className="flex flex-wrap gap-1 p-2 border-b border-slate-800 shrink-0">
            {([
              ["signal","SIGNAL"],["agents","AGENTS"],["options","OPTIONS"],
              ["news","NEWS"],["fear","F&G"],["accuracy","ACCURACY"]
            ] as const).map(([t, label]) => (
              <button key={t} onClick={() => setTab(t)} className={TAB_CLASSES(t)}>{label}</button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto p-3">

            {/* ── SIGNAL TAB ── */}
            {tab === "signal" && (
              <div className="space-y-4">
                {!judgment && !analyzing ? (
                  <div className="text-center py-8 space-y-2">
                    <div className="text-3xl">📊</div>
                    <p className="text-slate-400 text-sm">Enter a symbol and click Analyze</p>
                    <p className="text-slate-500 text-xs">9 agents will vote CALL / PUT / HOLD</p>
                    <p className="text-slate-600 text-xs">6 of 9 must agree to fire a signal</p>
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
                      <div className="mt-2">
                        <ConfRing pct={judgment.confidence} signal={judgment.signal} />
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
                    <p className="text-xs text-slate-500 bg-slate-800/50 rounded-lg p-2">{judgment.judge_reason}</p>
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
                    <div className="text-center mb-3">
                      <SignalBadge signal={judgment.signal} size="md" />
                    </div>
                    {[
                      ["🎯 Strike", judgment.strike_hint],
                      ["📅 Expiry", judgment.expiry_hint],
                      ["⚡ Entry Trigger", judgment.entry_trigger],
                      ["🛡️ Risk Note", judgment.risk_note],
                    ].map(([label, val]) => (
                      <div key={label as string} className="bg-slate-800/60 rounded-xl p-3 space-y-1">
                        <div className="text-xs text-slate-500 font-semibold">{label}</div>
                        <div className="text-sm text-slate-200 leading-relaxed">{val}</div>
                      </div>
                    ))}
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
                      <div className="text-xs text-slate-500 mb-1">POSITION SIZE (risk budget)</div>
                      <div className="text-xl font-black text-blue-400">{judgment.position_size_pct}% of account</div>
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
                    <div className="text-xs text-slate-500 font-semibold uppercase">Components</div>
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

                <div className="text-xs text-slate-500 font-semibold uppercase mt-4">Trump / Macro News</div>
                {fearLoading && <p className="text-xs text-slate-500 animate-pulse">Loading political news...</p>}
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
                <button onClick={loadFearGreed} className="w-full mt-2 text-xs text-blue-400 hover:text-blue-300 py-1">
                  ↻ Refresh Fear & Greed + News
                </button>
              </div>
            )}

            {/* ── ACCURACY TAB ── */}
            {tab === "accuracy" && (
              <div className="space-y-3">
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
