import { useState, useEffect, useRef, useCallback } from "react";

const API_BASE = "/api";

type Vote = "BUY" | "SELL" | "HOLD";

interface AgentVote {
  agent: string;
  emoji: string;
  vote: Vote;
  confidence: number;
  reason: string;
  stop_loss_long?: number;
  stop_loss_short?: number;
  target_long?: number;
  target_short?: number;
  atr?: number;
  volatility_pct?: number;
}

interface Judgment {
  signal: Vote;
  confidence: number;
  entry_price: number;
  stop_loss: number;
  target_price: number;
  agreed_agents: string[];
  disagreed_agents: string[];
  vote_tally: { BUY: number; SELL: number; HOLD: number };
  position_size_pct: number;
  judge_reason: string;
}

interface TickerData {
  symbol: string;
  price: number;
  prev_close: number;
  change_pct: number;
  volume: number;
  company_name: string;
}

type AnalysisState = "idle" | "connecting" | "streaming" | "complete" | "error";

const VOTE_COLORS: Record<Vote, { bg: string; text: string; border: string; glow: string }> = {
  BUY:  { bg: "bg-emerald-900/30", text: "text-emerald-300", border: "border-emerald-500/60", glow: "shadow-emerald-500/20" },
  SELL: { bg: "bg-red-900/30",     text: "text-red-300",     border: "border-red-500/60",     glow: "shadow-red-500/20"     },
  HOLD: { bg: "bg-yellow-900/20",  text: "text-yellow-300",  border: "border-yellow-500/40",  glow: "shadow-yellow-500/10"  },
};

const SIGNAL_STYLES: Record<Vote, string> = {
  BUY:  "from-emerald-500 to-green-400",
  SELL: "from-red-500 to-rose-400",
  HOLD: "from-yellow-500 to-amber-400",
};

function VoteBar({ vote, total }: { vote: number; total: number }) {
  const pct = total > 0 ? (vote / total) * 100 : 0;
  return (
    <div className="h-1.5 w-full bg-gray-800 rounded-full overflow-hidden">
      <div
        className="h-full bg-current rounded-full transition-all duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function AgentCard({ vote, isNew }: { vote: AgentVote; isNew?: boolean }) {
  const colors = VOTE_COLORS[vote.vote];
  return (
    <div
      className={`
        border rounded-xl p-4 transition-all duration-500
        ${colors.bg} ${colors.border}
        shadow-lg ${colors.glow}
        ${isNew ? "animate-pulse-once scale-[1.02]" : "scale-100"}
      `}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-xl">{vote.emoji}</span>
          <span className="font-semibold text-white text-sm">{vote.agent}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${colors.border} ${colors.text} bg-black/30`}>
            {vote.vote}
          </span>
          <span className="text-xs text-gray-400">{vote.confidence.toFixed(0)}%</span>
        </div>
      </div>

      {/* Confidence bar */}
      <div className="h-1 w-full bg-gray-800 rounded-full overflow-hidden mb-2">
        <div
          className={`h-full rounded-full transition-all duration-700 ${
            vote.vote === "BUY" ? "bg-emerald-400" : vote.vote === "SELL" ? "bg-red-400" : "bg-yellow-400"
          }`}
          style={{ width: `${vote.confidence}%` }}
        />
      </div>

      <p className="text-xs text-gray-400 leading-relaxed">{vote.reason}</p>
    </div>
  );
}

function SignalBadge({ signal, confidence }: { signal: Vote; confidence: number }) {
  const gradient = SIGNAL_STYLES[signal];
  return (
    <div className={`inline-flex items-center gap-3 px-6 py-3 rounded-2xl bg-gradient-to-r ${gradient} shadow-xl`}>
      <span className="text-3xl font-black text-white tracking-wider">{signal}</span>
      <span className="text-white/80 text-lg font-semibold">{confidence.toFixed(0)}%</span>
    </div>
  );
}

function StreamingDots() {
  return (
    <span className="inline-flex gap-1 ml-2">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </span>
  );
}

const POPULAR_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ", "AMZN", "GOOGL", "META", "BTC-USD"];

export default function TradingDashboard() {
  const [symbol, setSymbol] = useState("AAPL");
  const [inputSymbol, setInputSymbol] = useState("AAPL");
  const [state, setState] = useState<AnalysisState>("idle");
  const [ticker, setTicker] = useState<TickerData | null>(null);
  const [votes, setVotes] = useState<AgentVote[]>([]);
  const [newVoteIdx, setNewVoteIdx] = useState<number>(-1);
  const [judgment, setJudgment] = useState<Judgment | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const wsRef = useRef<WebSocket | null>(null);

  const runAnalysis = useCallback((sym: string) => {
    if (wsRef.current) {
      wsRef.current.close();
    }
    setVotes([]);
    setJudgment(null);
    setErrorMsg("");
    setTicker(null);
    setState("connecting");
    setNewVoteIdx(-1);

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}${API_BASE}/ws/analyze/${sym}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => setState("streaming");

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "ticker") {
        setTicker(msg.data);
      } else if (msg.type === "agent_vote") {
        setVotes((prev) => {
          const next = [...prev, msg.data];
          setNewVoteIdx(next.length - 1);
          setTimeout(() => setNewVoteIdx(-1), 800);
          return next;
        });
      } else if (msg.type === "judgment") {
        setJudgment(msg.data);
      } else if (msg.type === "complete") {
        setState("complete");
      } else if (msg.type === "error") {
        setErrorMsg(msg.message);
        setState("error");
      }
    };

    ws.onerror = () => {
      setErrorMsg("Connection failed. Check that the backend is running.");
      setState("error");
    };

    ws.onclose = () => {
      if (wsRef.current === ws) wsRef.current = null;
    };
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const sym = inputSymbol.trim().toUpperCase();
    if (!sym) return;
    setSymbol(sym);
    runAnalysis(sym);
  };

  useEffect(() => () => wsRef.current?.close(), []);

  const buyCount = votes.filter((v) => v.vote === "BUY").length;
  const sellCount = votes.filter((v) => v.vote === "SELL").length;
  const holdCount = votes.filter((v) => v.vote === "HOLD").length;
  const totalVotes = votes.length;

  return (
    <div className="min-h-screen bg-gray-950 text-white font-sans">
      {/* Header */}
      <div className="border-b border-gray-800 bg-gray-900/60 backdrop-blur-sm sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-2xl">⚡</span>
            <span className="font-bold text-lg tracking-tight">TradeSignal AI</span>
            <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded-full ml-1">8 Agents</span>
          </div>

          {/* Search */}
          <form onSubmit={handleSubmit} className="flex-1 flex gap-2 max-w-md ml-auto">
            <input
              className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm font-mono
                         focus:outline-none focus:border-blue-500 placeholder-gray-600 uppercase"
              value={inputSymbol}
              onChange={(e) => setInputSymbol(e.target.value.toUpperCase())}
              placeholder="Symbol (AAPL, TSLA…)"
            />
            <button
              type="submit"
              disabled={state === "streaming" || state === "connecting"}
              className="bg-blue-600 hover:bg-blue-500 disabled:bg-blue-900 disabled:text-blue-600
                         text-white text-sm font-semibold px-4 py-1.5 rounded-lg transition-colors"
            >
              {state === "connecting" ? "…" : state === "streaming" ? "Running" : "Analyze"}
            </button>
          </form>
        </div>

        {/* Quick symbols */}
        <div className="max-w-7xl mx-auto px-4 pb-2 flex gap-2 overflow-x-auto">
          {POPULAR_SYMBOLS.map((s) => (
            <button
              key={s}
              onClick={() => { setInputSymbol(s); setSymbol(s); runAnalysis(s); }}
              className={`text-xs px-2.5 py-1 rounded-md font-mono whitespace-nowrap transition-colors
                ${s === symbol && state !== "idle"
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white"}`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* Idle state */}
        {state === "idle" && (
          <div className="flex flex-col items-center justify-center py-24 gap-4">
            <span className="text-6xl">📊</span>
            <h2 className="text-2xl font-bold text-gray-200">Trading Prediction System</h2>
            <p className="text-gray-500 text-center max-w-md">
              Enter a ticker symbol above or pick a popular one. 8 specialized AI agents will analyze
              it and vote — a BUY or SELL fires only when 6 out of 8 agree.
            </p>
            <button
              onClick={() => runAnalysis("AAPL")}
              className="mt-4 bg-blue-600 hover:bg-blue-500 text-white font-semibold px-6 py-2.5 rounded-xl transition-colors"
            >
              Analyze AAPL →
            </button>
          </div>
        )}

        {/* Error */}
        {state === "error" && (
          <div className="bg-red-900/20 border border-red-800 rounded-xl p-4 text-red-400 text-sm">
            ⚠️ {errorMsg}
          </div>
        )}

        {/* Ticker bar */}
        {ticker && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 flex flex-wrap gap-6">
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-wide">Symbol</div>
              <div className="text-2xl font-black font-mono">{ticker.symbol}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-wide">Price</div>
              <div className="text-2xl font-bold">${ticker.price.toFixed(2)}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-wide">Change</div>
              <div className={`text-xl font-bold ${ticker.change_pct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {ticker.change_pct >= 0 ? "+" : ""}{ticker.change_pct.toFixed(2)}%
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-wide">Volume</div>
              <div className="text-xl font-semibold">{(ticker.volume / 1_000_000).toFixed(2)}M</div>
            </div>
            <div className="ml-auto text-right">
              <div className="text-xs text-gray-500">{ticker.company_name}</div>
              {(state === "streaming" || state === "connecting") && (
                <div className="text-xs text-blue-400 mt-1 flex items-center justify-end gap-1">
                  Agents voting<StreamingDots />
                </div>
              )}
            </div>
          </div>
        )}

        {/* Main layout: votes + judgment */}
        {votes.length > 0 && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            {/* Agent votes — 2/3 width */}
            <div className="xl:col-span-2 space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="font-bold text-gray-200">
                  Agent Votes
                  <span className="ml-2 text-sm text-gray-500">{votes.length}/7 agents reported</span>
                </h2>
                {/* Live tally */}
                <div className="flex items-center gap-3 text-sm">
                  <span className="text-emerald-400 font-bold">▲ {buyCount} BUY</span>
                  <span className="text-red-400 font-bold">▼ {sellCount} SELL</span>
                  <span className="text-yellow-400 font-bold">◆ {holdCount} HOLD</span>
                </div>
              </div>

              {/* Progress bars */}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
                <div className="flex items-center gap-3">
                  <span className="text-emerald-400 text-xs w-8">BUY</span>
                  <div className="flex-1 text-emerald-400"><VoteBar vote={buyCount} total={totalVotes} /></div>
                  <span className="text-xs text-gray-500 w-6">{buyCount}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-red-400 text-xs w-8">SELL</span>
                  <div className="flex-1 text-red-400"><VoteBar vote={sellCount} total={totalVotes} /></div>
                  <span className="text-xs text-gray-500 w-6">{sellCount}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-yellow-400 text-xs w-8">HOLD</span>
                  <div className="flex-1 text-yellow-400"><VoteBar vote={holdCount} total={totalVotes} /></div>
                  <span className="text-xs text-gray-500 w-6">{holdCount}</span>
                </div>
                {state === "streaming" && (
                  <p className="text-xs text-gray-600 pt-1">
                    Waiting for agents…{" "}
                    <span className="text-blue-400">Need 6/8 to agree for a signal</span>
                  </p>
                )}
              </div>

              {/* Agent cards grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {votes.map((vote, idx) => (
                  <AgentCard key={vote.agent} vote={vote} isNew={idx === newVoteIdx} />
                ))}

                {/* Placeholder skeleton cards */}
                {state === "streaming" &&
                  Array.from({ length: 7 - votes.length }).map((_, i) => (
                    <div
                      key={`skeleton-${i}`}
                      className="border border-gray-800 rounded-xl p-4 bg-gray-900/40 animate-pulse"
                    >
                      <div className="h-4 w-1/2 bg-gray-800 rounded mb-2" />
                      <div className="h-1 w-full bg-gray-800 rounded mb-3" />
                      <div className="h-3 w-3/4 bg-gray-800 rounded" />
                    </div>
                  ))}
              </div>
            </div>

            {/* Judgment panel — 1/3 width */}
            <div className="space-y-4">
              {/* Final Signal */}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 text-center">
                <h3 className="text-xs uppercase tracking-widest text-gray-500 mb-3">Final Signal</h3>
                {judgment ? (
                  <>
                    <SignalBadge signal={judgment.signal} confidence={judgment.confidence} />
                    <p className="text-xs text-gray-500 mt-3 leading-relaxed">{judgment.judge_reason}</p>
                  </>
                ) : (
                  <div className="text-gray-600 text-sm">
                    {state === "streaming" ? (
                      <><span>Awaiting all votes</span><StreamingDots /></>
                    ) : (
                      "—"
                    )}
                  </div>
                )}
              </div>

              {/* Trade details */}
              {judgment && (
                <>
                  <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
                    <h3 className="text-xs uppercase tracking-widest text-gray-500">Trade Details</h3>
                    <div className="space-y-2">
                      <div className="flex justify-between items-center">
                        <span className="text-xs text-gray-400">Entry Price</span>
                        <span className="font-mono text-white font-semibold">${judgment.entry_price.toFixed(2)}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-xs text-gray-400">Stop Loss</span>
                        <span className="font-mono text-red-400 font-semibold">${judgment.stop_loss.toFixed(2)}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-xs text-gray-400">Target Price</span>
                        <span className="font-mono text-emerald-400 font-semibold">${judgment.target_price.toFixed(2)}</span>
                      </div>
                      {judgment.signal !== "HOLD" && (
                        <>
                          <div className="border-t border-gray-800 pt-2">
                            <div className="flex justify-between items-center">
                              <span className="text-xs text-gray-400">Risk/Reward</span>
                              <span className="font-mono text-blue-400 font-semibold">
                                {judgment.entry_price > 0 && judgment.stop_loss !== judgment.entry_price
                                  ? (Math.abs(judgment.target_price - judgment.entry_price) /
                                     Math.abs(judgment.stop_loss - judgment.entry_price)).toFixed(2) + "x"
                                  : "—"}
                              </span>
                            </div>
                          </div>
                          <div className="flex justify-between items-center">
                            <span className="text-xs text-gray-400">Position Size</span>
                            <span className="font-mono text-purple-400 font-semibold">{judgment.position_size_pct}%</span>
                          </div>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Vote tally */}
                  <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                    <h3 className="text-xs uppercase tracking-widest text-gray-500 mb-3">Final Tally</h3>
                    <div className="flex justify-around text-center">
                      <div>
                        <div className="text-2xl font-black text-emerald-400">{judgment.vote_tally.BUY}</div>
                        <div className="text-xs text-gray-500">BUY</div>
                      </div>
                      <div>
                        <div className="text-2xl font-black text-red-400">{judgment.vote_tally.SELL}</div>
                        <div className="text-xs text-gray-500">SELL</div>
                      </div>
                      <div>
                        <div className="text-2xl font-black text-yellow-400">{judgment.vote_tally.HOLD}</div>
                        <div className="text-xs text-gray-500">HOLD</div>
                      </div>
                    </div>
                  </div>

                  {/* Agreed / Disagreed */}
                  {judgment.signal !== "HOLD" && (
                    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
                      {judgment.agreed_agents.length > 0 && (
                        <div>
                          <div className="text-xs text-emerald-400 font-semibold mb-1">✓ Agreed ({judgment.agreed_agents.length})</div>
                          <div className="space-y-0.5">
                            {judgment.agreed_agents.map((a) => (
                              <div key={a} className="text-xs text-gray-400">{a}</div>
                            ))}
                          </div>
                        </div>
                      )}
                      {judgment.disagreed_agents.length > 0 && (
                        <div>
                          <div className="text-xs text-red-400 font-semibold mb-1">✗ Disagreed ({judgment.disagreed_agents.length})</div>
                          <div className="space-y-0.5">
                            {judgment.disagreed_agents.map((a) => (
                              <div key={a} className="text-xs text-gray-400">{a}</div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>

      <style>{`
        @keyframes pulse-once {
          0% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4); }
          70% { box-shadow: 0 0 0 10px rgba(59, 130, 246, 0); }
          100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); }
        }
        .animate-pulse-once { animation: pulse-once 0.8s ease-out; }
      `}</style>
    </div>
  );
}
