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

// ─── Types ─────────────────────────────────────────────────────────────────
type Signal = "BUY_CALL" | "BUY_PUT" | "HOLD";

interface AgentVote {
  agent: string; emoji: string; vote: Signal;
  confidence: number; reason: string; weight?: number;
  stop_loss_long?: number; stop_loss_short?: number;
  target_long?: number; target_short?: number;
  atr?: number; volatility_pct?: number;
}

interface Judgment {
  signal: Signal;
  confidence: number;
  entry_price: number;
  stop_loss: number;
  target_price: number;
  agreed_agents: string[];
  disagreed_agents: string[];
  vote_tally: { BUY_CALL: number; BUY_PUT: number; HOLD: number };
  position_size_pct: number;
  judge_reason: string;
  action: string;
  strike_hint: string;
  expiry_hint: string;
  entry_trigger: string;
  risk_note: string;
  forecast_line?: { time: number; value: number }[];
}

interface LivePrice {
  symbol: string; price: number; change_pct: number;
  ts: number; market_state: string;
  news?: { title: string; summary: string; source: string; url: string; published_at: string }[];
}

interface Indicators {
  rsi14?: number; macd?: number; macd_signal?: number; macd_hist?: number;
  bb_upper?: number; bb_lower?: number; vwap?: number;
  atr14?: number; volatility_20d?: number; rel_volume?: number;
  trend_score?: number; supertrend_dir?: string;
  price_vs_vwap_pct?: number; change_1d?: number; change_5d?: number;
  stoch_k?: number; obv_slope_10d_pct?: number; up_dn_vol_ratio?: number;
}

interface ChartPoint { time: UTCTimestamp; open?: number; high?: number; low?: number; close?: number; value?: number; color?: string; }

// ─── Signal Styling ─────────────────────────────────────────────────────────
function signalStyle(v: Signal) {
  if (v === "BUY_CALL") return { bg: "bg-emerald-500/20", border: "border-emerald-500", text: "text-emerald-400", glow: "shadow-emerald-500/40" };
  if (v === "BUY_PUT")  return { bg: "bg-red-500/20",     border: "border-red-500",     text: "text-red-400",     glow: "shadow-red-500/40" };
  return { bg: "bg-slate-500/20", border: "border-slate-500", text: "text-slate-400", glow: "" };
}

function SignalBadge({ signal, size = "md" }: { signal: Signal; size?: "sm" | "md" | "lg" }) {
  const st = signalStyle(signal);
  const sz = size === "lg" ? "text-2xl px-8 py-3 font-black tracking-widest" : size === "md" ? "text-sm px-3 py-1 font-bold" : "text-xs px-2 py-0.5 font-semibold";
  const label = signal === "BUY_CALL" ? "⬆ CALL" : signal === "BUY_PUT" ? "⬇ PUT" : "⏸ HOLD";
  return (
    <span className={`rounded-full border ${st.bg} ${st.border} ${st.text} ${sz} shadow-lg ${st.glow}`}>
      {label}
    </span>
  );
}

// ─── Confidence Ring ─────────────────────────────────────────────────────────
function ConfRing({ pct, signal }: { pct: number; signal: Signal }) {
  const r = 44, circ = 2 * Math.PI * r;
  const dash = (pct / 100) * circ;
  const color = signal === "BUY_CALL" ? "#10b981" : signal === "BUY_PUT" ? "#ef4444" : "#64748b";
  return (
    <svg className="absolute inset-0" viewBox="0 0 100 100">
      <circle cx="50" cy="50" r={r} fill="none" stroke="#1e293b" strokeWidth="8" />
      <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="8"
        strokeDasharray={`${dash} ${circ - dash}`} strokeDashoffset={circ / 4}
        strokeLinecap="round" style={{ transition: "stroke-dasharray 0.6s ease" }} />
    </svg>
  );
}

// ─── Agent Card ──────────────────────────────────────────────────────────────
function AgentCard({ vote, isNew }: { vote: AgentVote; isNew?: boolean }) {
  const st = signalStyle(vote.vote);
  return (
    <div className={`rounded-xl border p-3 transition-all duration-500 ${isNew ? "scale-105 shadow-lg " + st.glow : ""} ${st.bg} ${st.border}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold text-slate-300 flex items-center gap-1">
          <span>{vote.emoji}</span> <span>{vote.agent}</span>
          {vote.weight !== undefined && vote.weight !== 1 && (
            <span className={`ml-1 text-[10px] ${vote.weight > 1 ? "text-emerald-400" : "text-red-400"}`}>
              {vote.weight > 1 ? "▲" : "▼"}{vote.weight.toFixed(2)}×
            </span>
          )}
        </span>
        <SignalBadge signal={vote.vote} size="sm" />
      </div>
      <div className="flex items-center gap-2">
        <div className="flex-1 bg-slate-700/50 rounded-full h-1.5">
          <div className={`h-1.5 rounded-full transition-all duration-700 ${vote.vote === "BUY_CALL" ? "bg-emerald-400" : vote.vote === "BUY_PUT" ? "bg-red-400" : "bg-slate-500"}`}
            style={{ width: `${vote.confidence}%` }} />
        </div>
        <span className={`text-xs font-mono font-bold ${st.text}`}>{vote.confidence.toFixed(0)}%</span>
      </div>
      <p className="text-[10px] text-slate-400 mt-1 leading-tight line-clamp-2">{vote.reason}</p>
    </div>
  );
}

// ─── Period Selector ─────────────────────────────────────────────────────────
function PeriodBtn({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className={`px-3 py-1 text-xs font-semibold rounded transition-all ${active ? "bg-cyan-500 text-black" : "bg-slate-700 text-slate-300 hover:bg-slate-600"}`}>
      {label}
    </button>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────
export default function TradingDashboard() {
  const [symbol, setSymbol] = useState("AAPL");
  const [inputSym, setInputSym] = useState("AAPL");
  const [status, setStatus] = useState("");
  const [votes, setVotes] = useState<AgentVote[]>([]);
  const [judgment, setJudgment] = useState<Judgment | null>(null);
  const [indicators, setIndicators] = useState<Indicators>({});
  const [livePrice, setLivePrice] = useState<LivePrice | null>(null);
  const [running, setRunning] = useState(false);
  const [activeTab, setActiveTab] = useState<"chart" | "agents" | "options" | "news" | "history">("chart");
  const [activeBottom, setActiveBottom] = useState<"volume" | "rsi" | "macd">("volume");
  const [period, setPeriod] = useState("3mo");
  const [showEma, setShowEma] = useState(true);
  const [showBB, setShowBB] = useState(false);
  const [showVwap, setShowVwap] = useState(true);
  const [showST, setShowST] = useState(false);
  const [history, setHistory] = useState<any[]>([]);
  const [newVoteIndex, setNewVoteIndex] = useState(-1);

  // Chart refs
  const mainChartRef = useRef<HTMLDivElement>(null);
  const bottomChartRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const bottomChartObjRef = useRef<IChartApi | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const forecastRef = useRef<any>(null);
  const targetLineRef = useRef<any>(null);
  const stopLineRef = useRef<any>(null);
  const seriesRefs = useRef<Record<string, any>>({});

  // ── Chart Setup ──────────────────────────────────────────────────────────
  const initCharts = useCallback(() => {
    if (!mainChartRef.current || !bottomChartRef.current) return;
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
    if (bottomChartObjRef.current) { bottomChartObjRef.current.remove(); bottomChartObjRef.current = null; }
    seriesRefs.current = {};

    const opts = {
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#94a3b8" },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#1e293b" },
      timeScale: { borderColor: "#1e293b", timeVisible: true },
    };

    const main = createChart(mainChartRef.current, { ...opts, height: 380 });
    const bottom = createChart(bottomChartRef.current, { ...opts, height: 120 });

    // Sync time scales
    main.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (r) bottom.timeScale().setVisibleLogicalRange(r);
    });
    bottom.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (r) main.timeScale().setVisibleLogicalRange(r);
    });

    chartRef.current = main;
    bottomChartObjRef.current = bottom;
  }, []);

  // ── Load Chart Data ──────────────────────────────────────────────────────
  const loadChart = useCallback(async (sym: string, p: string) => {
    if (!chartRef.current || !bottomChartObjRef.current) return;
    const main = chartRef.current;
    const bottom = bottomChartObjRef.current;

    // Remove old series
    Object.values(seriesRefs.current).forEach(s => {
      try { main.removeSeries(s); } catch (_) {}
      try { bottom.removeSeries(s); } catch (_) {}
    });
    seriesRefs.current = {};
    forecastRef.current = null;
    targetLineRef.current = null;
    stopLineRef.current = null;

    try {
      const interval = { "1d": "5m", "5d": "15m", "1mo": "1d", "3mo": "1d", "6mo": "1d" }[p] || "1d";
      const res = await fetch(`${API_BASE}/chart/${sym}?period=${p}&interval=${interval}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.error || !data.candles?.length) return;

      const toTS = (t: number) => t as UTCTimestamp;

      // Candlestick
      const candleSeries = main.addSeries(CandlestickSeries, {
        upColor: "#26a69a", downColor: "#ef5350",
        borderUpColor: "#26a69a", borderDownColor: "#ef5350",
        wickUpColor: "#26a69a", wickDownColor: "#ef5350",
      });
      candleSeries.setData(data.candles.map((c: any) => ({ ...c, time: toTS(c.time) })));
      seriesRefs.current.candle = candleSeries;

      // EMA 9
      if (showEma && data.ema?.length) {
        const e9 = main.addSeries(LineSeries, { color: "#f59e0b", lineWidth: 1, lineStyle: 0, crosshairMarkerVisible: false });
        const e21 = main.addSeries(LineSeries, { color: "#3b82f6", lineWidth: 1, lineStyle: 0, crosshairMarkerVisible: false });
        const e50 = main.addSeries(LineSeries, { color: "#8b5cf6", lineWidth: 1, lineStyle: 0, crosshairMarkerVisible: false });
        e9.setData(data.ema.filter((d: any) => d.ema9 != null).map((d: any) => ({ time: toTS(d.time), value: d.ema9 })));
        e21.setData(data.ema.filter((d: any) => d.ema21 != null).map((d: any) => ({ time: toTS(d.time), value: d.ema21 })));
        e50.setData(data.ema.filter((d: any) => d.ema50 != null).map((d: any) => ({ time: toTS(d.time), value: d.ema50 })));
        seriesRefs.current.e9 = e9; seriesRefs.current.e21 = e21; seriesRefs.current.e50 = e50;
      }

      // Bollinger Bands
      if (showBB && data.bb?.length) {
        const bbU = main.addSeries(LineSeries, { color: "rgba(99,102,241,0.6)", lineWidth: 1, lineStyle: 2, crosshairMarkerVisible: false });
        const bbM = main.addSeries(LineSeries, { color: "rgba(99,102,241,0.4)", lineWidth: 1, lineStyle: 1, crosshairMarkerVisible: false });
        const bbL = main.addSeries(LineSeries, { color: "rgba(99,102,241,0.6)", lineWidth: 1, lineStyle: 2, crosshairMarkerVisible: false });
        const bbFilter = (key: string) => data.bb.filter((d: any) => d[key] != null).map((d: any) => ({ time: toTS(d.time), value: d[key] }));
        bbU.setData(bbFilter("upper")); bbM.setData(bbFilter("mid")); bbL.setData(bbFilter("lower"));
        seriesRefs.current.bbU = bbU; seriesRefs.current.bbM = bbM; seriesRefs.current.bbL = bbL;
      }

      // VWAP
      if (showVwap && data.vwap?.length) {
        const vwapS = main.addSeries(LineSeries, { color: "#f97316", lineWidth: 2, lineStyle: 1, crosshairMarkerVisible: false });
        vwapS.setData(data.vwap.filter((d: any) => d.value != null).map((d: any) => ({ time: toTS(d.time), value: d.value })));
        seriesRefs.current.vwap = vwapS;
      }

      // SuperTrend
      if (showST && data.supertrend?.length) {
        const stBull = main.addSeries(LineSeries, { color: "#10b981", lineWidth: 2, crosshairMarkerVisible: false });
        const stBear = main.addSeries(LineSeries, { color: "#ef4444", lineWidth: 2, crosshairMarkerVisible: false });
        const stBullData = data.supertrend.filter((d: any) => d.direction === "up").map((d: any) => ({ time: toTS(d.time), value: d.value }));
        const stBearData = data.supertrend.filter((d: any) => d.direction === "down").map((d: any) => ({ time: toTS(d.time), value: d.value }));
        if (stBullData.length) stBull.setData(stBullData);
        if (stBearData.length) stBear.setData(stBearData);
        seriesRefs.current.stBull = stBull; seriesRefs.current.stBear = stBear;
      }

      // Bottom panel
      if (activeBottom === "volume" && data.volume?.length) {
        const volS = bottom.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "volume" });
        volS.setData(data.volume.map((d: any) => ({ time: toTS(d.time), value: d.value, color: d.color })));
        seriesRefs.current.vol = volS;
      } else if (activeBottom === "rsi" && data.rsi?.length) {
        const rsiS = bottom.addSeries(LineSeries, { color: "#a78bfa", lineWidth: 1 });
        rsiS.setData(data.rsi.map((d: any) => ({ time: toTS(d.time), value: d.value })));
        // OB/OS lines
        const rsiLast = data.rsi[data.rsi.length - 1];
        if (rsiLast) {
          const ob = bottom.addSeries(LineSeries, { color: "rgba(239,68,68,0.4)", lineWidth: 1, lineStyle: 2, crosshairMarkerVisible: false });
          const os = bottom.addSeries(LineSeries, { color: "rgba(16,185,129,0.4)", lineWidth: 1, lineStyle: 2, crosshairMarkerVisible: false });
          ob.setData([{ time: toTS(data.rsi[0].time), value: 70 }, { time: toTS(rsiLast.time), value: 70 }]);
          os.setData([{ time: toTS(data.rsi[0].time), value: 30 }, { time: toTS(rsiLast.time), value: 30 }]);
          seriesRefs.current.ob = ob; seriesRefs.current.os = os;
        }
        seriesRefs.current.rsi = rsiS;
      } else if (activeBottom === "macd" && data.macd?.length) {
        const macdLine = bottom.addSeries(LineSeries, { color: "#38bdf8", lineWidth: 1 });
        const sigLine = bottom.addSeries(LineSeries, { color: "#f97316", lineWidth: 1 });
        const macdHist = bottom.addSeries(HistogramSeries, {});
        macdLine.setData(data.macd.map((d: any) => ({ time: toTS(d.time), value: d.macd })));
        sigLine.setData(data.macd.map((d: any) => ({ time: toTS(d.time), value: d.signal })));
        macdHist.setData(data.macd.map((d: any) => ({ time: toTS(d.time), value: d.hist, color: d.hist >= 0 ? "#26a69a88" : "#ef535088" })));
        seriesRefs.current.macdLine = macdLine; seriesRefs.current.sigLine = sigLine; seriesRefs.current.macdHist = macdHist;
      }

      main.timeScale().fitContent();
      bottom.timeScale().fitContent();
    } catch (e) {
      console.error("Chart load error", e);
    }
  }, [showEma, showBB, showVwap, showST, activeBottom]);

  // ── Draw Prediction on Chart ──────────────────────────────────────────────
  const drawPrediction = useCallback((j: Judgment) => {
    const main = chartRef.current;
    if (!main || !j.forecast_line?.length) return;

    // Remove old forecast series
    if (forecastRef.current) { try { main.removeSeries(forecastRef.current); } catch (_) {} }
    if (targetLineRef.current) { try { main.removeSeries(targetLineRef.current); } catch (_) {} }
    if (stopLineRef.current) { try { main.removeSeries(stopLineRef.current); } catch (_) {} }

    const toTS = (t: number) => t as UTCTimestamp;
    const isCall = j.signal === "BUY_CALL";
    const isPut = j.signal === "BUY_PUT";

    if (j.signal !== "HOLD" && j.forecast_line.length > 0) {
      // Prediction path line — dotted, CALL=green PUT=red
      const predSeries = main.addSeries(LineSeries, {
        color: isCall ? "#10b981" : "#ef4444",
        lineWidth: 2,
        lineStyle: 2, // dashed
        crosshairMarkerVisible: true,
        lastValueVisible: true,
        title: "PREDICTION",
      });

      // Start from entry price, project to forecast
      const forecastData = [
        { time: toTS(j.forecast_line[0].time - 86400), value: j.entry_price },
        ...j.forecast_line.map(p => ({ time: toTS(p.time), value: p.value })),
      ];
      predSeries.setData(forecastData);
      forecastRef.current = predSeries;

      // Target line (horizontal)
      const targetS = main.addSeries(LineSeries, {
        color: isCall ? "rgba(16,185,129,0.7)" : "rgba(239,68,68,0.7)",
        lineWidth: 1, lineStyle: 3,
        crosshairMarkerVisible: false,
        lastValueVisible: true,
        title: `TARGET $${j.target_price}`,
      });
      const lastForecastTime = j.forecast_line[j.forecast_line.length - 1].time;
      targetS.setData([
        { time: toTS(j.forecast_line[0].time - 86400), value: j.target_price },
        { time: toTS(lastForecastTime), value: j.target_price },
      ]);
      targetLineRef.current = targetS;

      // Stop line
      const stopS = main.addSeries(LineSeries, {
        color: isPut ? "rgba(16,185,129,0.7)" : "rgba(239,68,68,0.7)",
        lineWidth: 1, lineStyle: 3,
        crosshairMarkerVisible: false,
        lastValueVisible: true,
        title: `STOP $${j.stop_loss}`,
      });
      stopS.setData([
        { time: toTS(j.forecast_line[0].time - 86400), value: j.stop_loss },
        { time: toTS(lastForecastTime), value: j.stop_loss },
      ]);
      stopLineRef.current = stopS;

      // Entry marker
      try {
        createSeriesMarkers(seriesRefs.current.candle || predSeries, [{
          time: toTS(j.forecast_line[0].time - 86400),
          position: isCall ? "belowBar" : "aboveBar",
          color: isCall ? "#10b981" : "#ef4444",
          shape: isCall ? "arrowUp" : "arrowDown",
          text: isCall ? `⬆ CALL @$${j.entry_price}` : `⬇ PUT @$${j.entry_price}`,
          size: 2,
        }]);
      } catch (_) {}
    }
  }, []);

  // ── WebSocket Analysis ──────────────────────────────────────────────────
  const runAnalysis = useCallback((sym: string) => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setVotes([]);
    setJudgment(null);
    setStatus("🔌 Connecting...");
    setRunning(true);

    const ws = new WebSocket(`${WS_BASE}/api/ws/analyze/${sym}`);
    wsRef.current = ws;

    ws.onopen = () => setStatus("🔍 Connected — fetching live data...");

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "status") {
          setStatus(msg.message);
        } else if (msg.type === "live_price") {
          setLivePrice(msg);
        } else if (msg.type === "agent_vote") {
          setVotes(prev => {
            const updated = [...prev, msg.vote];
            setNewVoteIndex(updated.length - 1);
            setTimeout(() => setNewVoteIndex(-1), 800);
            return updated;
          });
        } else if (msg.type === "judgment") {
          setJudgment(msg.judgment);
          setIndicators(msg.indicators || {});
          setRunning(false);
          setStatus("✅ Analysis complete");
          // Draw forecast on chart
          setTimeout(() => drawPrediction(msg.judgment), 200);
        } else if (msg.type === "error") {
          setStatus(`❌ ${msg.message}`);
          setRunning(false);
        }
      } catch (e) {
        console.error("WS parse error", e);
      }
    };

    ws.onerror = () => { setStatus("❌ Connection error"); setRunning(false); };
    ws.onclose = () => { if (running) setStatus("⚠️ Disconnected"); };

    // Keep-alive ping
    const pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 8000);
    ws.addEventListener("close", () => clearInterval(pingInterval));
  }, [drawPrediction]);

  // ── Effects ──────────────────────────────────────────────────────────────
  useEffect(() => {
    initCharts();
    return () => {
      chartRef.current?.remove();
      bottomChartObjRef.current?.remove();
    };
  }, []);

  useEffect(() => {
    if (chartRef.current) {
      loadChart(symbol, period);
    }
  }, [symbol, period, showEma, showBB, showVwap, showST, activeBottom]);

  // Reload chart when judgment comes in (to redraw everything cleanly)
  useEffect(() => {
    if (judgment && chartRef.current) {
      loadChart(symbol, period).then(() => drawPrediction(judgment));
    }
  }, [judgment]);

  // Load history
  useEffect(() => {
    if (activeTab === "history") {
      fetch(`${API_BASE}/learning/history/${symbol}`)
        .then(r => r.json()).then(d => setHistory(d.history || [])).catch(() => {});
    }
  }, [activeTab, symbol]);

  const handleAnalyze = () => {
    const sym = inputSym.trim().toUpperCase();
    if (!sym) return;
    setSymbol(sym);
    setActiveTab("chart");
    runAnalysis(sym);
  };

  const fmtPrice = (p?: number) => p != null ? `$${p.toFixed(2)}` : "--";
  const fmtPct = (p?: number) => p != null ? `${p > 0 ? "+" : ""}${p.toFixed(2)}%` : "--";

  const rr = judgment ? Math.abs(judgment.target_price - judgment.entry_price) /
    Math.abs(judgment.stop_loss - judgment.entry_price) : 0;

  return (
    <div className="min-h-screen bg-[#0a0f1e] text-slate-100 font-mono">
      {/* ── Top Bar ── */}
      <div className="border-b border-slate-800 bg-[#0d1629] px-4 py-2 flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center text-sm font-black">T</div>
          <div>
            <div className="text-sm font-black text-white">TradeSignal AI</div>
            <div className="text-[9px] text-slate-500">8-AGENT · CALL/PUT · REAL-TIME</div>
          </div>
        </div>

        {/* Symbol input */}
        <div className="flex gap-2 flex-1 min-w-[200px]">
          <input
            className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white w-32 uppercase focus:border-cyan-500 outline-none"
            value={inputSym}
            onChange={e => setInputSym(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === "Enter" && handleAnalyze()}
            placeholder="SYMBOL"
          />
          <button onClick={handleAnalyze}
            disabled={running}
            className={`px-4 py-1.5 rounded-lg text-sm font-bold transition-all ${running ? "bg-slate-700 text-slate-500 cursor-not-allowed" : "bg-cyan-500 hover:bg-cyan-400 text-black"}`}>
            {running ? "⏳" : "Analyze"}
          </button>
        </div>

        {/* Quick symbols */}
        <div className="flex gap-1.5 flex-wrap">
          {["AAPL","NVDA","TSLA","MSFT","SPY","QQQ","AMZN","META","AMD","COIN"].map(s => (
            <button key={s} onClick={() => { setInputSym(s); setSymbol(s); runAnalysis(s); }}
              className={`px-2 py-1 rounded text-xs font-bold transition-all border ${s === symbol ? "border-cyan-500 bg-cyan-500/20 text-cyan-300" : "border-slate-600 text-slate-400 hover:border-slate-400 hover:text-white"}`}>
              {s}
            </button>
          ))}
        </div>

        {/* Live price */}
        {livePrice && (
          <div className="ml-auto flex items-center gap-3 text-right">
            <div>
              <div className="text-xl font-black text-white">{fmtPrice(livePrice.price)}</div>
              <div className={`text-xs font-bold ${livePrice.change_pct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {fmtPct(livePrice.change_pct)} · {livePrice.market_state}
                <span className="ml-2 inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Status Bar ── */}
      {status && (
        <div className="px-4 py-1.5 bg-slate-900 border-b border-slate-800 text-xs text-cyan-400 font-mono flex items-center gap-2">
          {running && <span className="inline-block w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />}
          {status}
        </div>
      )}

      <div className="flex h-[calc(100vh-88px)]">
        {/* ── Left Panel: Chart + Bottom ── */}
        <div className="flex-1 min-w-0 flex flex-col border-r border-slate-800">
          {/* Chart Controls */}
          <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-800 bg-[#0d1629] flex-wrap">
            {/* Period */}
            <div className="flex gap-1">
              {[["1d","1D"],["5d","5D"],["1mo","1M"],["3mo","3M"],["6mo","6M"]].map(([p,l]) => (
                <PeriodBtn key={p} label={l} active={period === p} onClick={() => setPeriod(p)} />
              ))}
            </div>
            <div className="w-px h-4 bg-slate-700" />
            {/* Overlays */}
            {[
              ["EMA", showEma, () => setShowEma(v => !v)],
              ["BB", showBB, () => setShowBB(v => !v)],
              ["VWAP", showVwap, () => setShowVwap(v => !v)],
              ["ST", showST, () => setShowST(v => !v)],
            ].map(([lbl, active, fn]) => (
              <button key={lbl as string} onClick={fn as any}
                className={`px-2 py-0.5 text-[10px] rounded border transition-all ${active ? "border-cyan-500 text-cyan-400 bg-cyan-500/10" : "border-slate-600 text-slate-500"}`}>
                {lbl as string}
              </button>
            ))}
            <div className="ml-auto flex gap-1">
              {([["volume","VOL"],["rsi","RSI"],["macd","MACD"]] as const).map(([k,l]) => (
                <PeriodBtn key={k} label={l} active={activeBottom === k} onClick={() => setActiveBottom(k)} />
              ))}
            </div>
          </div>

          {/* Main Chart */}
          <div ref={mainChartRef} className="w-full" style={{ height: 380 }} />

          {/* Bottom Chart */}
          <div className="border-t border-slate-800 px-2 pt-1">
            <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-0.5">
              {activeBottom === "volume" ? "Volume" : activeBottom === "rsi" ? "RSI (14) — OB:70 / OS:30" : "MACD (12/26/9)"}
            </div>
          </div>
          <div ref={bottomChartRef} className="w-full" style={{ height: 120 }} />

          {/* Legend */}
          <div className="px-3 py-1.5 border-t border-slate-800 bg-[#0d1629] flex gap-3 flex-wrap text-[10px]">
            {showEma && (<>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-amber-400 inline-block" /> EMA9</span>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-blue-400 inline-block" /> EMA21</span>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-violet-400 inline-block" /> EMA50</span>
            </>)}
            {showVwap && <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-orange-400 inline-block" style={{borderTop: "1px dashed"}} /> VWAP</span>}
            {showST && <>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-emerald-400 inline-block" /> ST Bull</span>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-red-400 inline-block" /> ST Bear</span>
            </>}
            {judgment && judgment.signal !== "HOLD" && (
              <>
                <span className="flex items-center gap-1"><span className="w-3 h-0.5 inline-block" style={{borderTop: `2px dashed ${judgment.signal === "BUY_CALL" ? "#10b981" : "#ef4444"}`}} /> PREDICTION</span>
                <span className="text-emerald-400">▶ TARGET ${judgment.target_price.toFixed(2)}</span>
                <span className="text-red-400">◀ STOP ${judgment.stop_loss.toFixed(2)}</span>
              </>
            )}
          </div>
        </div>

        {/* ── Right Panel ── */}
        <div className="w-[380px] flex flex-col overflow-hidden bg-[#0d1629]">
          {/* Tab Bar */}
          <div className="flex border-b border-slate-800">
            {(["chart","agents","options","news","history"] as const).map(t => (
              <button key={t} onClick={() => setActiveTab(t)}
                className={`flex-1 py-2 text-[10px] font-bold uppercase tracking-wider transition-all ${activeTab === t ? "text-cyan-400 border-b-2 border-cyan-400 bg-cyan-500/5" : "text-slate-500 hover:text-slate-300"}`}>
                {t === "agents" ? `Agents${votes.length ? ` (${votes.length}/7)` : ""}` : t === "chart" ? "Signal" : t}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {/* ── SIGNAL TAB ── */}
            {activeTab === "chart" && (
              <div className="space-y-3">
                {!judgment && !running && (
                  <div className="text-center py-16 text-slate-600">
                    <div className="text-4xl mb-3">📊</div>
                    <div className="text-sm">Enter a symbol and click Analyze</div>
                    <div className="text-xs mt-1">8 agents will vote CALL / PUT / HOLD</div>
                    <div className="text-xs text-slate-700 mt-2">6 of 7 must agree to fire a signal</div>
                  </div>
                )}
                {running && !judgment && (
                  <div className="text-center py-8">
                    <div className="text-3xl animate-spin mb-3 inline-block">⚙️</div>
                    <div className="text-sm text-cyan-400">{status}</div>
                    <div className="mt-4 space-y-1">
                      {votes.map((v, i) => (
                        <div key={i} className="flex items-center justify-between text-xs px-2 py-1 bg-slate-800 rounded">
                          <span>{v.emoji} {v.agent}</span>
                          <SignalBadge signal={v.vote} size="sm" />
                          <span className="text-slate-400">{v.confidence.toFixed(0)}%</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {judgment && (
                  <>
                    {/* Big Signal */}
                    <div className={`rounded-2xl border-2 p-4 text-center ${signalStyle(judgment.signal).border} ${signalStyle(judgment.signal).bg}`}>
                      <div className="relative w-24 h-24 mx-auto mb-3">
                        <ConfRing pct={judgment.confidence} signal={judgment.signal} />
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                          <div className={`text-2xl font-black ${signalStyle(judgment.signal).text}`}>
                            {judgment.confidence.toFixed(0)}%
                          </div>
                          <div className="text-[9px] text-slate-500">CONF</div>
                        </div>
                      </div>
                      <SignalBadge signal={judgment.signal} size="lg" />
                      <div className="mt-2 text-xs text-slate-400">{judgment.judge_reason}</div>
                    </div>

                    {/* Vote Tally */}
                    <div className="grid grid-cols-3 gap-2 text-center">
                      {[
                        ["⬆ CALL", judgment.vote_tally.BUY_CALL, "text-emerald-400 bg-emerald-500/10 border-emerald-700"],
                        ["⬇ PUT", judgment.vote_tally.BUY_PUT, "text-red-400 bg-red-500/10 border-red-700"],
                        ["⏸ HOLD", judgment.vote_tally.HOLD, "text-slate-400 bg-slate-500/10 border-slate-700"],
                      ].map(([lbl, val, cls]) => (
                        <div key={lbl as string} className={`rounded-lg border p-2 ${cls}`}>
                          <div className="text-lg font-black">{val}</div>
                          <div className="text-[10px]">{lbl}</div>
                        </div>
                      ))}
                    </div>

                    {/* Price Levels */}
                    <div className="bg-slate-800/50 rounded-xl p-3 space-y-2">
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider">Price Levels</div>
                      {[
                        ["Entry", fmtPrice(judgment.entry_price), "text-white"],
                        ["Target", fmtPrice(judgment.target_price), "text-emerald-400"],
                        ["Stop", fmtPrice(judgment.stop_loss), "text-red-400"],
                        ["R/R Ratio", rr > 0 ? `${rr.toFixed(1)}:1` : "—", rr >= 2 ? "text-emerald-400" : "text-amber-400"],
                        ["Position", `${judgment.position_size_pct}% of portfolio`, "text-cyan-400"],
                      ].map(([lbl, val, cls]) => (
                        <div key={lbl as string} className="flex justify-between items-center">
                          <span className="text-xs text-slate-400">{lbl}</span>
                          <span className={`text-sm font-bold font-mono ${cls}`}>{val}</span>
                        </div>
                      ))}
                    </div>

                    {/* Key Indicators */}
                    <div className="bg-slate-800/50 rounded-xl p-3 space-y-2">
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider">Key Indicators</div>
                      {[
                        ["RSI 14", indicators.rsi14?.toFixed(1), indicators.rsi14 != null ? (indicators.rsi14 > 70 ? "text-red-400" : indicators.rsi14 < 30 ? "text-emerald-400" : "text-white") : "text-white"],
                        ["Stochastic", indicators.stoch_k?.toFixed(1), indicators.stoch_k != null ? (indicators.stoch_k > 80 ? "text-red-400" : indicators.stoch_k < 20 ? "text-emerald-400" : "text-white") : "text-white"],
                        ["SuperTrend", indicators.supertrend_dir || "—", indicators.supertrend_dir === "up" ? "text-emerald-400" : "text-red-400"],
                        ["VWAP dist", indicators.price_vs_vwap_pct != null ? `${indicators.price_vs_vwap_pct > 0 ? "+" : ""}${indicators.price_vs_vwap_pct?.toFixed(2)}%` : "—", (indicators.price_vs_vwap_pct ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"],
                        ["OBV slope", indicators.obv_slope_10d_pct != null ? `${indicators.obv_slope_10d_pct > 0 ? "+" : ""}${indicators.obv_slope_10d_pct?.toFixed(1)}%` : "—", (indicators.obv_slope_10d_pct ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"],
                        ["Rel Volume", indicators.rel_volume?.toFixed(2) + "×", (indicators.rel_volume ?? 1) > 1.5 ? "text-amber-400" : "text-white"],
                        ["Trend Score", indicators.trend_score != null ? `${indicators.trend_score > 0 ? "+" : ""}${indicators.trend_score}/3` : "—", (indicators.trend_score ?? 0) > 0 ? "text-emerald-400" : (indicators.trend_score ?? 0) < 0 ? "text-red-400" : "text-white"],
                        ["Volatility", indicators.volatility_20d?.toFixed(1) + "%", (indicators.volatility_20d ?? 25) > 40 ? "text-red-400" : "text-white"],
                      ].map(([lbl, val, cls]) => (
                        <div key={lbl as string} className="flex justify-between">
                          <span className="text-xs text-slate-400">{lbl}</span>
                          <span className={`text-xs font-mono font-bold ${cls}`}>{val ?? "—"}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}

            {/* ── AGENTS TAB ── */}
            {activeTab === "agents" && (
              <div className="space-y-2">
                {votes.length === 0 && (
                  <div className="text-center text-slate-600 py-12 text-sm">
                    No votes yet — run an analysis first
                  </div>
                )}
                {votes.map((v, i) => (
                  <AgentCard key={v.agent} vote={v} isNew={i === newVoteIndex} />
                ))}
                {judgment && (
                  <div className={`rounded-xl border-2 p-3 ${signalStyle(judgment.signal).border} ${signalStyle(judgment.signal).bg}`}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-bold flex items-center gap-1">⚖️ Judge Agent</span>
                      <SignalBadge signal={judgment.signal} size="md" />
                    </div>
                    <p className="text-xs text-slate-300">{judgment.judge_reason}</p>
                    {judgment.agreed_agents.length > 0 && (
                      <div className="mt-2 text-[10px] text-slate-400">
                        ✅ Agreed: {judgment.agreed_agents.join(", ")}
                      </div>
                    )}
                    {judgment.disagreed_agents.length > 0 && (
                      <div className="mt-1 text-[10px] text-slate-500">
                        ❌ Disagreed: {judgment.disagreed_agents.join(", ")}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* ── OPTIONS TAB ── */}
            {activeTab === "options" && (
              <div className="space-y-3">
                {!judgment ? (
                  <div className="text-center text-slate-600 py-12 text-sm">Run analysis to see options details</div>
                ) : (
                  <>
                    <div className={`rounded-2xl border-2 p-4 ${signalStyle(judgment.signal).border} ${signalStyle(judgment.signal).bg}`}>
                      <div className="text-center mb-3">
                        <SignalBadge signal={judgment.signal} size="lg" />
                      </div>
                      <div className="grid grid-cols-2 gap-3 mt-3">
                        {[
                          ["Recommended", judgment.action?.replace("_"," ") || "—"],
                          ["Strike", judgment.strike_hint],
                          ["Expiry", judgment.expiry_hint],
                          ["Confidence", `${judgment.confidence.toFixed(1)}%`],
                        ].map(([k,v]) => (
                          <div key={k} className="bg-black/20 rounded-lg p-2 text-center">
                            <div className="text-[9px] text-slate-500 uppercase">{k}</div>
                            <div className={`text-sm font-bold mt-0.5 ${signalStyle(judgment.signal).text}`}>{v}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="bg-slate-800/50 rounded-xl p-3 space-y-3">
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider">Entry Trigger</div>
                      <p className="text-xs text-slate-200 leading-relaxed">{judgment.entry_trigger}</p>
                    </div>
                    <div className="bg-slate-800/50 rounded-xl p-3 space-y-2">
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider">Risk Management</div>
                      <p className="text-xs text-amber-300 leading-relaxed">{judgment.risk_note}</p>
                    </div>
                    <div className="bg-slate-800/50 rounded-xl p-3 space-y-2">
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider">Price Targets</div>
                      {[
                        ["Entry", fmtPrice(judgment.entry_price), "text-white"],
                        ["Target (3×ATR)", fmtPrice(judgment.target_price), "text-emerald-400"],
                        ["Stop (2×ATR)", fmtPrice(judgment.stop_loss), "text-red-400"],
                        ["R/R", rr > 0 ? `${rr.toFixed(2)}:1` : "—", rr >= 2 ? "text-emerald-400" : "text-amber-400"],
                      ].map(([l,v,c]) => (
                        <div key={l} className="flex justify-between">
                          <span className="text-xs text-slate-400">{l}</span>
                          <span className={`text-sm font-mono font-bold ${c}`}>{v}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}

            {/* ── NEWS TAB ── */}
            {activeTab === "news" && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-slate-500 uppercase tracking-wider">Live News — {symbol}</span>
                  <span className="flex items-center gap-1 text-[9px] text-emerald-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" /> live
                  </span>
                </div>
                {(!livePrice?.news || livePrice.news.length === 0) && (
                  <div className="text-center text-slate-600 py-8 text-sm">
                    No news loaded — run an analysis first
                  </div>
                )}
                {livePrice?.news?.map((n, i) => (
                  <a key={i} href={n.url} target="_blank" rel="noopener noreferrer"
                    className="block bg-slate-800/50 rounded-xl p-3 hover:bg-slate-700/50 transition-all border border-slate-700/50">
                    <div className="text-xs font-semibold text-slate-200 leading-snug mb-1">{n.title}</div>
                    {n.summary && <p className="text-[10px] text-slate-400 line-clamp-2 leading-relaxed">{n.summary}</p>}
                    <div className="mt-1.5 flex items-center gap-2 text-[9px] text-slate-500">
                      <span className="text-cyan-500">{n.source}</span>
                      <span>·</span>
                      <span>{n.published_at ? new Date(n.published_at).toLocaleTimeString() : "recent"}</span>
                    </div>
                  </a>
                ))}
              </div>
            )}

            {/* ── HISTORY TAB ── */}
            {activeTab === "history" && (
              <div className="space-y-2">
                <div className="text-[10px] text-slate-500 uppercase tracking-wider">
                  Prediction History — {symbol}
                </div>
                {history.length === 0 && (
                  <div className="text-center text-slate-600 py-8 text-sm">
                    No predictions saved yet
                  </div>
                )}
                {history.map((h, i) => (
                  <div key={i} className="bg-slate-800/50 rounded-xl p-3 border border-slate-700/50">
                    <div className="flex items-center justify-between mb-1">
                      <SignalBadge signal={h.signal} size="sm" />
                      <span className={`text-xs font-bold ${h.outcome === "CORRECT" ? "text-emerald-400" : h.outcome === "WRONG" ? "text-red-400" : "text-slate-400"}`}>
                        {h.outcome || "PENDING"}
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-1 text-[10px]">
                      <div><span className="text-slate-500">Entry:</span> <span className="text-white">${h.entry_price?.toFixed(2)}</span></div>
                      <div><span className="text-slate-500">Target:</span> <span className="text-emerald-400">${h.target_price?.toFixed(2)}</span></div>
                      <div><span className="text-slate-500">Conf:</span> <span className="text-cyan-400">{h.confidence?.toFixed(0)}%</span></div>
                    </div>
                    <div className="text-[9px] text-slate-600 mt-1">
                      {h.timestamp ? new Date(h.timestamp * 1000).toLocaleString() : "—"}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
