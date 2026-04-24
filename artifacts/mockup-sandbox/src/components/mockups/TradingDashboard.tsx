import { useState, useEffect, useRef, useCallback } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  createSeriesMarkers,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";

const API_BASE = "/api";

// ─── Types ───────────────────────────────────────────────────────────────────
type Vote = "BUY" | "SELL" | "HOLD";

interface AgentVote {
  agent: string; emoji: string; vote: Vote;
  confidence: number; reason: string;
  weight?: number; accuracy?: number; predictions_tracked?: number;
}

interface Judgment {
  signal: Vote; confidence: number;
  entry_price: number; stop_loss: number; target_price: number;
  agreed_agents: string[]; disagreed_agents: string[];
  vote_tally: { BUY: number; SELL: number; HOLD: number };
  position_size_pct: number; judge_reason: string; prediction_id?: number;
}

interface TickerData {
  symbol: string; price: number; prev_close: number;
  change_pct: number; volume: number; avg_volume: number;
  company_name: string; sector: string;
  week_52_high: number; week_52_low: number;
}

interface Candle {
  time: UTCTimestamp; open: number; high: number; low: number; close: number; volume: number;
}

interface ChartIndicators {
  ema9: { time: UTCTimestamp; value: number }[];
  ema21: { time: UTCTimestamp; value: number }[];
  ema50: { time: UTCTimestamp; value: number }[];
  bb_upper: { time: UTCTimestamp; value: number }[];
  bb_lower: { time: UTCTimestamp; value: number }[];
  bb_mid: { time: UTCTimestamp; value: number }[];
  rsi: { time: UTCTimestamp; value: number }[];
  macd_line: { time: UTCTimestamp; value: number }[];
  macd_signal: { time: UTCTimestamp; value: number }[];
  macd_hist: { time: UTCTimestamp; value: number }[];
  volume: { time: UTCTimestamp; value: number }[];
}

interface ChartData { symbol: string; candles: Candle[]; indicators: ChartIndicators; }
type AnalysisState = "idle" | "connecting" | "streaming" | "complete" | "error";
type ChartState = "idle" | "loading" | "ready" | "error";

// ─── Config ──────────────────────────────────────────────────────────────────
const POPULAR = ["AAPL", "NVDA", "MSFT", "TSLA", "SPY", "QQQ", "AMZN", "META", "BTC-USD", "ETH-USD"];

const VOTE_CFG: Record<Vote, { bg: string; border: string; text: string; bar: string }> = {
  BUY:  { bg: "bg-emerald-950/60", border: "border-emerald-500/40", text: "text-emerald-300", bar: "bg-emerald-400" },
  SELL: { bg: "bg-red-950/60",     border: "border-red-500/40",     text: "text-red-300",     bar: "bg-red-400"     },
  HOLD: { bg: "bg-amber-950/40",   border: "border-amber-500/30",   text: "text-amber-300",   bar: "bg-amber-400"   },
};

// ─── Helpers ─────────────────────────────────────────────────────────────────
const fmt = (n: number, d = 2) => isFinite(n) ? n.toFixed(d) : "—";
const fmtK = (n: number) =>
  n >= 1e9 ? (n / 1e9).toFixed(2) + "B" :
  n >= 1e6 ? (n / 1e6).toFixed(2) + "M" :
  n >= 1e3 ? (n / 1e3).toFixed(1) + "K" : String(n);

const CHART_THEME = {
  layout: { background: { type: ColorType.Solid, color: "#08080d" }, textColor: "rgba(255,255,255,0.45)", fontFamily: "monospace", fontSize: 11 },
  grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.04)" } },
  crosshair: { mode: CrosshairMode.Normal, vertLine: { color: "rgba(255,255,255,0.3)", labelBackgroundColor: "#1a1a2e" }, horzLine: { color: "rgba(255,255,255,0.3)", labelBackgroundColor: "#1a1a2e" } },
  rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
  timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, secondsVisible: false },
};

// ─── Small components ─────────────────────────────────────────────────────────
function PulsingDot() {
  return (
    <span className="relative flex h-2 w-2">
      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
      <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500" />
    </span>
  );
}
function Dots() {
  return (
    <span className="inline-flex gap-0.5 ml-1">
      {[0,1,2].map(i=>(
        <span key={i} className="w-1 h-1 bg-current rounded-full animate-bounce" style={{animationDelay:`${i*120}ms`}} />
      ))}
    </span>
  );
}

function AgentCard({ v }: { v: AgentVote }) {
  const c = VOTE_CFG[v.vote];
  return (
    <div className={`border rounded-xl p-3.5 transition-all duration-300 ${c.bg} ${c.border}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-lg">{v.emoji}</span>
          <span className="text-xs font-semibold text-white/80">{v.agent}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs font-black px-2 py-0.5 rounded-full border ${c.border} ${c.text} bg-black/30`}>{v.vote}</span>
          <span className="text-xs text-white/40">{fmt(v.confidence, 0)}%</span>
        </div>
      </div>
      <div className="h-1 bg-white/5 rounded-full overflow-hidden mb-2">
        <div className={`h-full rounded-full transition-all duration-700 ${c.bar}`} style={{ width: `${Math.min(v.confidence, 100)}%` }} />
      </div>
      <p className="text-[11px] text-white/40 leading-relaxed line-clamp-2">{v.reason}</p>
      {(v.predictions_tracked ?? 0) > 0 && (
        <div className="flex items-center gap-3 mt-2 pt-2 border-t border-white/5 text-[10px]">
          <span className="text-white/30">Accuracy: <span className="text-white/50">{fmt((v.accuracy ?? 0.5)*100, 0)}%</span></span>
          <span className="text-white/30">Weight: <span className={`${(v.weight??1)>1?"text-emerald-400":(v.weight??1)<0.9?"text-red-400":"text-white/50"}`}>{fmt(v.weight??1, 2)}x</span></span>
          <span className="ml-auto text-white/25">{v.predictions_tracked} tracked</span>
        </div>
      )}
    </div>
  );
}

function SignalPanel({ j }: { j: Judgment }) {
  const colors = { BUY: "text-emerald-400", SELL: "text-red-400", HOLD: "text-amber-400" };
  const shadows = { BUY: "0 0 40px rgba(52,211,153,0.3)", SELL: "0 0 40px rgba(248,113,113,0.3)", HOLD: "0 0 30px rgba(251,191,36,0.2)" };
  const rr = j.entry_price && Math.abs(j.stop_loss - j.entry_price) > 0
    ? Math.abs(j.target_price - j.entry_price) / Math.abs(j.stop_loss - j.entry_price) : 0;

  return (
    <div className="space-y-4">
      {/* Big signal */}
      <div className="text-center py-5">
        <div className={`text-5xl font-black tracking-widest ${colors[j.signal]}`} style={{textShadow: shadows[j.signal]}}>{j.signal}</div>
        <div className="text-xl font-bold text-white mt-1">{fmt(j.confidence)}%</div>
        <p className="text-[11px] text-white/35 mt-1.5 leading-relaxed px-2">{j.judge_reason}</p>
      </div>

      {/* Trade levels */}
      <div className="bg-white/3 border border-white/8 rounded-xl overflow-hidden">
        <div className="grid grid-cols-3 divide-x divide-white/8">
          {[{label:"ENTRY",val:`$${fmt(j.entry_price)}`,col:"text-white"},
            {label:"STOP",val:`$${fmt(j.stop_loss)}`,col:"text-red-400"},
            {label:"TARGET",val:`$${fmt(j.target_price)}`,col:"text-emerald-400"}]
            .map(({label,val,col})=>(
              <div key={label} className="p-3 text-center">
                <div className="text-[9px] text-white/35 uppercase tracking-widest mb-1">{label}</div>
                <div className={`text-sm font-bold font-mono ${col}`}>{val}</div>
              </div>
            ))}
        </div>
        {rr > 0 && (
          <div className="border-t border-white/8 px-4 py-2 flex justify-between">
            <span className="text-[11px] text-white/35">Risk / Reward</span>
            <span className={`text-[11px] font-bold ${rr>=2?"text-emerald-400":rr>=1.5?"text-amber-400":"text-red-400"}`}>{fmt(rr,2)}x</span>
          </div>
        )}
        {j.signal !== "HOLD" && (
          <div className="border-t border-white/8 px-4 py-2 flex justify-between">
            <span className="text-[11px] text-white/35">Position Size</span>
            <span className="text-[11px] font-bold text-purple-400">{j.position_size_pct}%</span>
          </div>
        )}
      </div>

      {/* Tally */}
      <div className="space-y-2">
        {(["BUY","SELL","HOLD"] as Vote[]).map(v => {
          const n = j.vote_tally[v];
          const total = Object.values(j.vote_tally).reduce((a,b)=>a+b,0);
          const c = VOTE_CFG[v];
          return (
            <div key={v} className="flex items-center gap-2">
              <span className={`text-[11px] font-bold w-9 ${c.text}`}>{v}</span>
              <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
                <div className={`h-full rounded-full transition-all duration-700 ${c.bar}`} style={{width:`${total>0?(n/total)*100:0}%`}} />
              </div>
              <span className="text-xs text-white/35">{n}/8</span>
            </div>
          );
        })}
      </div>

      {/* Agreed / disagreed */}
      {j.signal !== "HOLD" && (
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-emerald-950/30 border border-emerald-500/15 rounded-lg p-2.5">
            <div className="text-[10px] text-emerald-400 font-bold mb-1">✓ For ({j.agreed_agents.length})</div>
            {j.agreed_agents.map(a => <div key={a} className="text-[10px] text-white/40 truncate">{a}</div>)}
          </div>
          <div className="bg-red-950/30 border border-red-500/15 rounded-lg p-2.5">
            <div className="text-[10px] text-red-400 font-bold mb-1">✗ Against ({j.disagreed_agents.length})</div>
            {j.disagreed_agents.map(a => <div key={a} className="text-[10px] text-white/40 truncate">{a}</div>)}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Chart ────────────────────────────────────────────────────────────────────
function MainChart({ chartData, chartState, judgment, showEMA, showBB }: {
  chartData: ChartData | null; chartState: ChartState;
  judgment: Judgment | null; showEMA: boolean; showBB: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => () => { chartRef.current?.remove(); }, []);

  useEffect(() => {
    if (!containerRef.current || !chartData) return;
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }

    const el = containerRef.current;
    const chart = createChart(el, {
      ...CHART_THEME,
      width: el.clientWidth,
      height: el.clientHeight,
    } as any);
    chartRef.current = chart;

    // Candlestick
    const cs = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e", downColor: "#ef4444",
      borderUpColor: "#22c55e", borderDownColor: "#ef4444",
      wickUpColor: "#22c55e", wickDownColor: "#ef4444",
    });
    cs.setData(chartData.candles as any);

    // EMA
    if (showEMA) {
      const e9 = chart.addSeries(LineSeries, { color:"#f59e0b", lineWidth:1, title:"EMA9", priceLineVisible:false });
      e9.setData(chartData.indicators.ema9 as any);
      const e21 = chart.addSeries(LineSeries, { color:"#3b82f6", lineWidth:1, title:"EMA21", priceLineVisible:false });
      e21.setData(chartData.indicators.ema21 as any);
      const e50 = chart.addSeries(LineSeries, { color:"#8b5cf6", lineWidth:1, lineStyle:2, title:"EMA50", priceLineVisible:false });
      e50.setData(chartData.indicators.ema50 as any);
    }

    // Bollinger
    if (showBB) {
      const bbu = chart.addSeries(LineSeries, { color:"rgba(99,102,241,0.55)", lineWidth:1, lineStyle:1, title:"BB+", priceLineVisible:false });
      bbu.setData(chartData.indicators.bb_upper as any);
      const bbl = chart.addSeries(LineSeries, { color:"rgba(99,102,241,0.55)", lineWidth:1, lineStyle:1, title:"BB-", priceLineVisible:false });
      bbl.setData(chartData.indicators.bb_lower as any);
      const bbm = chart.addSeries(LineSeries, { color:"rgba(99,102,241,0.3)", lineWidth:1, lineStyle:2, title:"MA20", priceLineVisible:false });
      bbm.setData(chartData.indicators.bb_mid as any);
    }

    // Price lines & markers
    if (judgment && chartData.candles.length > 0) {
      const last = chartData.candles[chartData.candles.length - 1];
      const mColor = judgment.signal === "BUY" ? "#22c55e" : judgment.signal === "SELL" ? "#ef4444" : "#f59e0b";
      createSeriesMarkers(cs, [{
        time: last.time,
        position: judgment.signal === "BUY" ? "belowBar" : judgment.signal === "SELL" ? "aboveBar" : "inBar",
        color: mColor,
        shape: judgment.signal === "BUY" ? "arrowUp" : judgment.signal === "SELL" ? "arrowDown" : "circle",
        text: `${judgment.signal} ${fmt(judgment.confidence, 0)}%`,
        size: 2,
      }]);

      if (judgment.signal !== "HOLD") {
        cs.createPriceLine({ price: judgment.entry_price, color:"rgba(255,255,255,0.45)", lineWidth:1, lineStyle:2, axisLabelVisible:true, title:"Entry" });
        cs.createPriceLine({ price: judgment.stop_loss, color:"#ef4444", lineWidth:1, lineStyle:2, axisLabelVisible:true, title:"Stop" });
        cs.createPriceLine({ price: judgment.target_price, color:"#22c55e", lineWidth:1, lineStyle:2, axisLabelVisible:true, title:"Target" });
      }
    }

    chart.timeScale().fitContent();

    const obs = new ResizeObserver(() => { if (chartRef.current) chartRef.current.applyOptions({ width: el.clientWidth }); });
    obs.observe(el);
    return () => obs.disconnect();
  }, [chartData, showEMA, showBB, judgment]);

  return (
    <div className="w-full h-full relative rounded-xl overflow-hidden border border-white/6">
      {chartState === "loading" && (
        <div className="absolute inset-0 bg-[#08080d] flex items-center justify-center z-10">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            <span className="text-xs text-white/30">Loading chart data…</span>
          </div>
        </div>
      )}
      {chartState === "error" && (
        <div className="absolute inset-0 bg-[#08080d] flex items-center justify-center z-10">
          <p className="text-xs text-red-400">Chart load failed</p>
        </div>
      )}
      <div ref={containerRef} className="w-full h-full" />
    </div>
  );
}

function SubChart({ data, active }: { data: ChartData; active: "volume" | "rsi" | "macd" }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => () => { chartRef.current?.remove(); }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }

    const el = containerRef.current;
    const chart = createChart(el, {
      ...CHART_THEME,
      width: el.clientWidth,
      height: el.clientHeight,
    } as any);
    chartRef.current = chart;

    if (active === "volume") {
      const s = chart.addSeries(HistogramSeries, { color:"rgba(59,130,246,0.5)", priceFormat:{ type:"volume" } });
      s.setData(data.indicators.volume.map(d => ({ ...d, color: "rgba(59,130,246,0.5)" })) as any);
    } else if (active === "rsi") {
      const s = chart.addSeries(LineSeries, { color:"#f59e0b", lineWidth:2, title:"RSI" });
      s.setData(data.indicators.rsi as any);
      const ob = chart.addSeries(LineSeries, { color:"rgba(239,68,68,0.35)", lineWidth:1, lineStyle:2, priceLineVisible:false });
      ob.setData(data.indicators.rsi.map(d => ({...d, value:70})) as any);
      const os = chart.addSeries(LineSeries, { color:"rgba(34,197,94,0.35)", lineWidth:1, lineStyle:2, priceLineVisible:false });
      os.setData(data.indicators.rsi.map(d => ({...d, value:30})) as any);
    } else {
      const ml = chart.addSeries(LineSeries, { color:"#3b82f6", lineWidth:2, title:"MACD" });
      ml.setData(data.indicators.macd_line as any);
      const sl = chart.addSeries(LineSeries, { color:"#f59e0b", lineWidth:1, title:"Signal" });
      sl.setData(data.indicators.macd_signal as any);
      const hs = chart.addSeries(HistogramSeries, { title:"Hist", priceFormat:{ type:"price", precision:4, minMove:0.0001 } });
      hs.setData(data.indicators.macd_hist.map(d => ({ ...d, color: d.value >= 0 ? "rgba(34,197,94,0.6)" : "rgba(239,68,68,0.6)" })) as any);
    }

    chart.timeScale().fitContent();
    const obs = new ResizeObserver(() => { if (chartRef.current) chartRef.current.applyOptions({ width: el.clientWidth }); });
    obs.observe(el);
    return () => obs.disconnect();
  }, [data, active]);

  return <div ref={containerRef} className="w-full h-full rounded-xl overflow-hidden border border-white/6" />;
}

// ─── Main App ─────────────────────────────────────────────────────────────────
export default function TradingDashboard() {
  const [inputVal, setInputVal] = useState("AAPL");
  const [currentSym, setCurrentSym] = useState("");
  const [analysisState, setAnalysisState] = useState<AnalysisState>("idle");
  const [chartState, setChartState] = useState<ChartState>("idle");
  const [ticker, setTicker] = useState<TickerData | null>(null);
  const [votes, setVotes] = useState<AgentVote[]>([]);
  const [judgment, setJudgment] = useState<Judgment | null>(null);
  const [chartData, setChartData] = useState<ChartData | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"chart"|"agents"|"history">("chart");
  const [showEMA, setShowEMA] = useState(true);
  const [showBB, setShowBB] = useState(false);
  const [bottomPanel, setBottomPanel] = useState<"volume"|"rsi"|"macd">("volume");
  const [history, setHistory] = useState<any[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  const loadChart = useCallback(async (sym: string) => {
    setChartState("loading");
    setChartData(null);
    try {
      const r = await fetch(`${API_BASE}/chart/${sym}`);
      if (!r.ok) throw new Error("chart error");
      setChartData(await r.json());
      setChartState("ready");
    } catch { setChartState("error"); }
  }, []);

  const loadHistory = useCallback(async (sym: string) => {
    try {
      const r = await fetch(`${API_BASE}/learning/history/${sym}`);
      if (r.ok) { const d = await r.json(); setHistory(d.predictions || []); }
    } catch {}
  }, []);

  const runAnalysis = useCallback((sym: string) => {
    const s = sym.trim().toUpperCase();
    if (!s) return;
    wsRef.current?.close();
    setVotes([]); setJudgment(null); setError(""); setTicker(null);
    setCurrentSym(s); setAnalysisState("connecting");

    loadChart(s);

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}${API_BASE}/ws/analyze/${s}`);
    wsRef.current = ws;
    ws.onopen = () => setAnalysisState("streaming");
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.type === "ticker") setTicker(m.data);
      else if (m.type === "agent_vote") setVotes(p => [...p, m.data]);
      else if (m.type === "judgment") { setJudgment(m.data); loadHistory(s); }
      else if (m.type === "complete") setAnalysisState("complete");
      else if (m.type === "error") { setError(m.message); setAnalysisState("error"); }
    };
    ws.onerror = () => { setError("WebSocket error — backend may be loading, try again"); setAnalysisState("error"); };
    ws.onclose = () => { if (wsRef.current === ws) wsRef.current = null; };
  }, [loadChart, loadHistory]);

  useEffect(() => () => wsRef.current?.close(), []);

  const submit = (e: React.FormEvent) => { e.preventDefault(); runAnalysis(inputVal); };
  const isRunning = analysisState === "connecting" || analysisState === "streaming";
  const active = analysisState === "streaming" || analysisState === "complete";

  return (
    <div className="h-screen bg-[#08080d] text-white flex flex-col overflow-hidden" style={{fontFamily:"system-ui,-apple-system,sans-serif"}}>
      {/* Header */}
      <header className="flex-none border-b border-white/6 bg-[#0c0c14]/90 backdrop-blur px-4 py-2">
        <div className="flex items-center gap-3">
          {/* Brand */}
          <div className="flex items-center gap-2 shrink-0">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-600 to-violet-700 flex items-center justify-center shadow-lg shadow-blue-900/40">
              <span className="text-xs font-black">T</span>
            </div>
            <div className="leading-none">
              <div className="text-sm font-bold">TradeSignal AI</div>
              <div className="text-[9px] text-white/30 tracking-widest">8-AGENT CONSENSUS</div>
            </div>
          </div>

          {/* Search */}
          <form onSubmit={submit} className="flex gap-2 max-w-xs flex-1">
            <input
              className="flex-1 bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-sm font-mono uppercase tracking-wide
                         placeholder-white/20 focus:outline-none focus:border-blue-500/50 transition-colors"
              value={inputVal} onChange={e => setInputVal(e.target.value.toUpperCase())}
              placeholder="AAPL, TSLA, BTC-USD…"
            />
            <button type="submit" disabled={isRunning}
              className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-sm font-semibold px-4 rounded-lg transition-all">
              {isRunning ? "…" : "Analyze"}
            </button>
          </form>

          {/* Quick picks */}
          <div className="flex gap-1 overflow-x-auto" style={{scrollbarWidth:"none"}}>
            {POPULAR.map(s => (
              <button key={s} onClick={() => { setInputVal(s); runAnalysis(s); }}
                className={`text-[11px] px-2.5 py-1 rounded-md font-mono whitespace-nowrap border transition-all
                  ${s === currentSym
                    ? "bg-blue-600/25 border-blue-500/45 text-blue-300"
                    : "border-white/8 text-white/38 hover:border-white/18 hover:text-white/65"}`}>
                {s}
              </button>
            ))}
          </div>

          {isRunning && (
            <div className="flex items-center gap-1.5 ml-auto shrink-0">
              <PulsingDot /><span className="text-xs text-blue-400">LIVE</span>
            </div>
          )}
        </div>
      </header>

      {/* Ticker bar */}
      {ticker && (
        <div className="flex-none border-b border-white/5 bg-[#0d0d17]/60 px-4 py-2">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
            <div className="flex items-baseline gap-2">
              <span className="font-black font-mono text-lg">{ticker.symbol}</span>
              <span className="text-white/35 text-xs truncate max-w-[160px]">{ticker.company_name}</span>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-xl font-bold">${fmt(ticker.price)}</span>
              <span className={`text-sm font-semibold ${ticker.change_pct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {ticker.change_pct >= 0 ? "+" : ""}{fmt(ticker.change_pct)}%
              </span>
            </div>
            {[
              {l:"Vol",v:fmtK(ticker.volume)},
              {l:"Avg",v:fmtK(ticker.avg_volume)},
              ticker.week_52_high > 0 ? {l:"52W H",v:`$${fmt(ticker.week_52_high)}`} : null,
              ticker.week_52_low > 0 ? {l:"52W L",v:`$${fmt(ticker.week_52_low)}`} : null,
              ticker.sector ? {l:"Sector",v:ticker.sector} : null,
            ].filter(Boolean).map(({l,v}: any) => (
              <span key={l} className="text-xs"><span className="text-white/30">{l} </span><span className="text-white/55">{v}</span></span>
            ))}
            {/* 52-week range bar */}
            {ticker.week_52_high > 0 && ticker.week_52_low > 0 && (
              <div className="ml-auto flex items-center gap-2">
                <span className="text-[10px] text-white/25">52W Range</span>
                <div className="w-20 h-1.5 bg-white/8 rounded-full overflow-hidden">
                  <div className="h-full rounded-full bg-gradient-to-r from-red-500 via-amber-400 to-emerald-400 transition-all duration-700"
                    style={{ width: `${Math.min(100, Math.max(0, ((ticker.price - ticker.week_52_low) / (ticker.week_52_high - ticker.week_52_low)) * 100))}%` }} />
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Body */}
      <div className="flex-1 min-h-0 flex overflow-hidden">

        {/* ── Idle ── */}
        {analysisState === "idle" && (
          <div className="flex-1 flex flex-col items-center justify-center gap-6 p-8">
            <div className="relative">
              <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-blue-600/15 to-violet-600/15 border border-white/10 flex items-center justify-center">
                <span className="text-4xl">📊</span>
              </div>
              <div className="absolute -top-1.5 -right-1.5 w-6 h-6 bg-blue-600 rounded-full flex items-center justify-center text-[10px] font-black shadow-lg shadow-blue-900/50">8</div>
            </div>
            <div className="text-center">
              <h1 className="text-3xl font-bold">TradeSignal AI</h1>
              <p className="text-white/40 text-sm mt-2 max-w-xs leading-relaxed">
                8 specialized AI agents analyze every trade. BUY/SELL fires only when <span className="text-blue-400 font-semibold">6 of 8 agree</span>.
              </p>
            </div>
            <div className="flex flex-wrap justify-center gap-3">
              {["AAPL","NVDA","TSLA","SPY","BTC-USD"].map(s => (
                <button key={s} onClick={() => { setInputVal(s); runAnalysis(s); }}
                  className="bg-white/5 hover:bg-white/10 border border-white/10 hover:border-white/20
                             px-5 py-2.5 rounded-xl font-mono text-sm font-semibold transition-all hover:shadow-lg hover:shadow-white/5">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ── Error ── */}
        {analysisState === "error" && (
          <div className="flex-1 flex items-center justify-center">
            <div className="bg-red-950/40 border border-red-800/40 rounded-2xl p-8 text-center max-w-sm">
              <div className="text-3xl mb-3">⚠️</div>
              <p className="text-red-400 text-sm">{error}</p>
              <button onClick={() => setAnalysisState("idle")} className="mt-4 text-xs text-white/30 hover:text-white underline">Go back</button>
            </div>
          </div>
        )}

        {/* ── Active ── */}
        {active && (
          <>
            {/* Left: Tabs + content */}
            <div className="flex-1 min-w-0 flex flex-col p-3 gap-2">
              {/* Tab bar */}
              <div className="flex items-center gap-0 border-b border-white/6">
                {[
                  {k:"chart", label:"Chart"},
                  {k:"agents", label:`Agents ${votes.length}/7`, badge: isRunning},
                  {k:"history", label:"History"},
                ].map(({k,label,badge}) => (
                  <button key={k} onClick={() => setTab(k as any)}
                    className={`px-4 py-2 text-xs font-semibold border-b-2 transition-colors -mb-px ${
                      tab===k ? "border-blue-500 text-white" : "border-transparent text-white/35 hover:text-white/65"}`}>
                    {label}{badge && <Dots />}
                  </button>
                ))}

                {/* Indicator toggles (chart only) */}
                {tab === "chart" && (
                  <div className="ml-auto flex items-center gap-2">
                    {[{l:"EMA",v:showEMA,fn:()=>setShowEMA(p=>!p)},{l:"BB",v:showBB,fn:()=>setShowBB(p=>!p)}].map(({l,v,fn}) => (
                      <button key={l} onClick={fn}
                        className={`text-[11px] px-2 py-0.5 rounded border transition-colors ${v?"bg-blue-600/25 border-blue-500/45 text-blue-300":"border-white/10 text-white/30 hover:text-white/55"}`}>
                        {l}
                      </button>
                    ))}
                    <div className="w-px h-4 bg-white/10 mx-1" />
                    {(["volume","rsi","macd"] as const).map(t => (
                      <button key={t} onClick={() => setBottomPanel(t)}
                        className={`text-[11px] px-2 py-0.5 rounded border uppercase transition-colors ${bottomPanel===t?"bg-white/10 border-white/25 text-white":"border-white/8 text-white/30 hover:text-white/55"}`}>
                        {t}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Tab content */}
              <div className="flex-1 min-h-0">
                {tab === "chart" && (
                  <div className="flex flex-col h-full gap-2">
                    <div className="flex-1 min-h-0">
                      <MainChart chartData={chartData} chartState={chartState} judgment={judgment} showEMA={showEMA} showBB={showBB} />
                    </div>
                    {chartData && (
                      <div className="h-28 flex-none">
                        <SubChart data={chartData} active={bottomPanel} />
                      </div>
                    )}
                  </div>
                )}

                {tab === "agents" && (
                  <div className="h-full overflow-y-auto space-y-2.5 pr-1">
                    {votes.map(v => <AgentCard key={v.agent} v={v} />)}
                    {isRunning && Array.from({length: 7 - votes.length}).map((_,i) => (
                      <div key={i} className="border border-white/5 rounded-xl p-3.5 bg-white/2 animate-pulse">
                        <div className="flex gap-2 mb-2"><div className="w-6 h-6 bg-white/8 rounded" /><div className="h-4 w-36 bg-white/8 rounded" /></div>
                        <div className="h-1 bg-white/5 rounded mb-2" /><div className="h-3 w-3/4 bg-white/5 rounded" />
                      </div>
                    ))}
                  </div>
                )}

                {tab === "history" && (
                  <div className="h-full overflow-y-auto">
                    {history.length === 0 ? (
                      <div className="flex flex-col items-center justify-center h-full text-center gap-2">
                        <span className="text-2xl">📋</span>
                        <p className="text-white/35 text-sm">No tracked predictions for {currentSym} yet.</p>
                        <p className="text-white/20 text-xs max-w-xs">Predictions are saved automatically. Outcomes are verified after 24h and used to improve agent weights.</p>
                      </div>
                    ) : (
                      <table className="w-full text-xs">
                        <thead><tr className="text-white/30 border-b border-white/8 text-left">
                          {["Date","Signal","Confidence","Entry","Stop","Target","Outcome"].map(h => (
                            <th key={h} className="pb-2 pr-3 font-medium">{h}</th>
                          ))}
                        </tr></thead>
                        <tbody>
                          {history.map((p: any) => (
                            <tr key={p.id} className="border-b border-white/4 hover:bg-white/2 transition-colors">
                              <td className="py-2 pr-3 text-white/35 font-mono">{new Date(p.created_at).toLocaleDateString()}</td>
                              <td className="py-2 pr-3">
                                <span className={`font-black ${p.signal==="BUY"?"text-emerald-400":p.signal==="SELL"?"text-red-400":"text-amber-400"}`}>{p.signal}</span>
                              </td>
                              <td className="py-2 pr-3 text-white/45">{fmt(p.confidence)}%</td>
                              <td className="py-2 pr-3 font-mono text-white/55">${fmt(p.entry_price)}</td>
                              <td className="py-2 pr-3 font-mono text-red-400">${fmt(p.stop_loss)}</td>
                              <td className="py-2 pr-3 font-mono text-emerald-400">${fmt(p.target_price)}</td>
                              <td className="py-2">
                                {p.outcome === "CORRECT" ? <span className="text-emerald-400 font-bold">✓ Win</span>
                                 : p.outcome === "WRONG" ? <span className="text-red-400 font-bold">✗ Loss</span>
                                 : <span className="text-white/20">Pending</span>}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Right sidebar */}
            <div className="w-72 flex-none border-l border-white/6 flex flex-col overflow-hidden">
              <div className="flex-1 overflow-y-auto p-3">
                {judgment ? (
                  <SignalPanel j={judgment} />
                ) : (
                  <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
                    <div className="w-12 h-12 border-2 border-blue-500/40 border-t-blue-500 rounded-full animate-spin" />
                    <div>
                      <p className="text-sm font-semibold text-white/60">Agents voting…</p>
                      <p className="text-xs text-white/30 mt-1">{votes.length} of 7 reported</p>
                    </div>
                    {votes.length > 0 && (
                      <div className="w-full space-y-1.5">
                        {(["BUY","SELL","HOLD"] as Vote[]).map(v => {
                          const n = votes.filter(x=>x.vote===v).length;
                          const c = VOTE_CFG[v];
                          return (
                            <div key={v} className="flex items-center gap-2">
                              <span className={`text-[10px] font-bold w-8 ${c.text}`}>{v}</span>
                              <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
                                <div className={`h-full rounded-full transition-all duration-500 ${c.bar}`} style={{width:`${(n/7)*100}%`}} />
                              </div>
                              <span className="text-[10px] text-white/30">{n}</span>
                            </div>
                          );
                        })}
                        <p className="text-[10px] text-blue-400/50 mt-2">Needs 6/8 to fire</p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      <style>{`
        *::-webkit-scrollbar { width:4px; height:4px; }
        *::-webkit-scrollbar-track { background: transparent; }
        *::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius:2px; }
        .line-clamp-2 { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
      `}</style>
    </div>
  );
}
