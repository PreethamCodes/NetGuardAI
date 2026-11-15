import { useEffect, useMemo, useRef, useState } from "react";
import { api, openLiveSocket } from "./api";
import { AnomalyChart, CongestionChart, FlowsChart, LatencyChart, ProtocolChart, ResourceChart, ThroughputChart } from "./components/Charts";
import { ModelPerformance } from "./components/ModelPerformance";
import { Card, Empty, Kpi, Pill } from "./components/ui";
import { SEVERITY_TONE, TONE, condition, num, pct, toPoint } from "./lib";
import type { AlertItem, ModelsResponse, MonitorRecord, RunInfo, Timeline } from "./types";

type Mode = "replay" | "live";
type Tab = "monitor" | "performance";

export default function App() {
  const [mode, setMode] = useState<Mode>("replay");
  const [tab, setTab] = useState<Tab>("monitor");
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [runId, setRunId] = useState<string>("");
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(10);
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [advice, setAdvice] = useState<Record<string, string>>({});
  const [liveRecords, setLiveRecords] = useState<MonitorRecord[]>([]);
  const [liveConnected, setLiveConnected] = useState(false);
  const [error, setError] = useState<string>("");

  // ---- initial data
  useEffect(() => {
    api.runs().then((r) => {
      setRuns(r);
      const first = r.find((x) => x.split === "test" && x.scenario === "ramp") ?? r.find((x) => x.split === "test") ?? r[0];
      if (first) setRunId(first.experiment_id);
    }).catch((e: Error) => setError(`Cannot reach the backend (${e.message}). Start it with: python -m netguard serve`));
    api.models().then(setModels).catch(() => undefined);
    api.recommendations().then(setAdvice).catch(() => undefined);
  }, []);

  // ---- load the selected recorded run
  useEffect(() => {
    if (!runId) return;
    setPlaying(false);
    setCursor(0);
    api.timeline(runId).then(setTimeline).catch((e: Error) => setError(e.message));
  }, [runId]);

  // ---- playback of the recorded run (historical)
  const total = timeline?.records.length ?? 0;
  const tick = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!playing || mode !== "replay") return;
    tick.current = window.setInterval(() => {
      setCursor((c) => {
        if (c + 1 >= total) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, 1000 / speed);
    return () => window.clearInterval(tick.current);
  }, [playing, speed, total, mode]);

  // ---- live feed
  useEffect(() => {
    if (mode !== "live") return;
    api.liveRecords().then((r) => setLiveRecords(r.records)).catch(() => undefined);
    return openLiveSocket((rec) => setLiveRecords((prev) => [...prev.slice(-299), rec]), setLiveConnected);
  }, [mode]);

  const records: MonitorRecord[] = useMemo(
    () => (mode === "replay" ? (timeline?.records.slice(0, cursor + 1) ?? []) : liveRecords),
    [mode, timeline, cursor, liveRecords],
  );
  const points = useMemo(() => records.map(toPoint), [records]);
  const current = records[records.length - 1];
  const cond = condition(current);
  const alerts: AlertItem[] = useMemo(() => {
    if (mode === "replay") return (timeline?.alerts ?? []).filter((a) => a.second <= cursor).slice(-12).reverse();
    return liveRecords.flatMap((r) => (r as MonitorRecord & { alerts?: AlertItem[] }).alerts ?? []).slice(-12).reverse();
  }, [mode, timeline, cursor, liveRecords]);
  const run = runs.find((r) => r.experiment_id === runId);
  const pred = current?.predictions;
  const gt = current?.ground_truth;

  return (
    <div className="min-h-screen">
      <header className="bg-slate-900 text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">NetGuard AI</h1>
            <p className="text-xs text-slate-400">Network traffic analysis, anomaly detection and 30-second congestion prediction</p>
          </div>
          <nav className="flex gap-1 rounded-lg bg-slate-800 p-1 text-sm">
            {(["replay", "live"] as Mode[]).map((m) => (
              <button key={m} onClick={() => setMode(m)} className={`rounded-md px-3 py-1 ${mode === m ? "bg-white text-slate-900" : "text-slate-300 hover:text-white"}`}>
                {m === "replay" ? "Historical replay" : "Live monitoring"}
              </button>
            ))}
          </nav>
          <nav className="flex gap-1 text-sm">
            {(["monitor", "performance"] as Tab[]).map((t) => (
              <button key={t} onClick={() => setTab(t)} className={`rounded-md px-3 py-1 ${tab === t ? "bg-blue-600" : "text-slate-300 hover:text-white"}`}>
                {t === "monitor" ? "Monitoring" : "Model performance"}
              </button>
            ))}
          </nav>
        </div>
        <div className={`px-4 py-1.5 text-center text-xs font-medium ${mode === "replay" ? "bg-amber-400 text-amber-950" : "bg-emerald-500 text-emerald-950"}`}>
          {mode === "replay"
            ? "HISTORICAL DATA: replay of a recorded Mininet experiment run. Nothing on this screen is live."
            : `LIVE MONITORING: samples posted to the backend as they happen (${liveConnected ? "connected" : "not connected"}).`}
        </div>
      </header>

      <main className="mx-auto grid max-w-7xl gap-4 px-4 py-4">
        {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-800 ring-1 ring-red-200">{error}</div>}

        {tab === "performance" && <ModelPerformance models={models} />}

        {tab === "monitor" && (
          <>
            {mode === "replay" && (
              <Card title="Recorded run">
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <select value={runId} onChange={(e) => setRunId(e.target.value)} className="rounded-md border border-slate-300 bg-white px-2 py-1.5">
                    {runs.map((r) => (
                      <option key={r.experiment_id} value={r.experiment_id}>
                        {r.scenario_label} · run {r.repetition} · {r.split}
                      </option>
                    ))}
                  </select>
                  <button onClick={() => setPlaying((p) => !p)} disabled={!total} className="rounded-md bg-blue-600 px-3 py-1.5 font-medium text-white disabled:opacity-40">
                    {playing ? "Pause" : cursor + 1 >= total ? "Replay" : "Play"}
                  </button>
                  {cursor + 1 >= total && total > 0 && !playing && (
                    <button onClick={() => { setCursor(0); setPlaying(true); }} className="rounded-md border border-slate-300 px-3 py-1.5">Restart</button>
                  )}
                  <label className="flex items-center gap-1.5">
                    speed
                    <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="rounded-md border border-slate-300 bg-white px-1.5 py-1">
                      {[1, 5, 10, 30, 60].map((s) => <option key={s} value={s}>{s}×</option>)}
                    </select>
                  </label>
                  <input type="range" min={0} max={Math.max(total - 1, 0)} value={cursor} onChange={(e) => { setPlaying(false); setCursor(Number(e.target.value)); }} className="min-w-48 flex-1" />
                  <span className="tabular-nums text-slate-500">second {cursor} / {Math.max(total - 1, 0)}</span>
                </div>
                {run && (
                  <p className={`mt-2 text-xs ${run.seen_by_models ? "text-amber-700" : "text-emerald-700"}`}>
                    {run.seen_by_models
                      ? `This ${run.split} run was used to train or tune the models, so their predictions on it are optimistic. Choose a "test" run for an unseen example.`
                      : "Held-out test run: the models never saw this run during training or tuning."}
                  </p>
                )}
              </Card>
            )}

            {mode === "live" && records.length === 0 && (
              <Empty>
                No live samples received yet. Connect a monitor that sends one JSON sample per second to <code>POST /api/ingest</code>. The dashboard shows nothing until real samples arrive.
              </Empty>
            )}

            {records.length > 0 && (
              <>
                <Card
                  title="Network overview"
                  subtitle={mode === "replay" ? `Recorded ${run?.scenario_label ?? ""} run` : "Latest live sample"}
                  right={<Pill className={`ring-1 ${TONE[cond.tone]}`}>{cond.label}</Pill>}
                >
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
                    <Kpi label="Throughput" value={num(current?.metrics.throughput_mbps, 2, " Mbps")} />
                    <Kpi label="Utilisation" value={num(current?.metrics.utilisation_pct, 0, " %")} hint="of the 10 Mbps bottleneck" />
                    <Kpi label="Packet loss" value={num(current?.metrics.loss_pct, 1, " %")} hint={`probe ${num(current?.metrics.probe_loss_pct, 1, " %")}`} />
                    <Kpi label="RTT" value={num(current?.metrics.rtt_mean_ms, 0, " ms")} />
                    <Kpi label="Jitter" value={num(current?.metrics.jitter_ms, 1, " ms")} />
                    <Kpi label="VM CPU" value={num(current?.metrics.cpu_percent, 0, " %")} />
                    <Kpi label="VM memory" value={num(current?.metrics.ram_percent, 0, " %")} />
                  </div>
                </Card>

                <div className="grid gap-4 lg:grid-cols-2">
                  <Card title="Traffic: throughput and utilisation"><ThroughputChart data={points} /></Card>
                  <Card title="Latency, queue delay and loss"><LatencyChart data={points} /></Card>
                  <Card title="Flows and connections" subtitle="From NFStream flow records and ss">
                    <FlowsChart data={points} />
                  </Card>
                  <Card title="Protocol mix (this second)" subtitle="TShark packet counts; DNS is part of UDP and HTTP part of TCP">
                    <ProtocolChart record={current} />
                    <ResourceChart data={points} />
                  </Card>
                </div>

                <div className="grid gap-4 lg:grid-cols-2">
                  <Card
                    title="Congestion prediction"
                    subtitle="Probability that a new congestion episode starts within the next 30 seconds"
                    right={pred?.available && pred.congestion.warning && !pred.congestion.currently_congested
                      ? <Pill className="bg-red-600 text-white">WARNING</Pill> : undefined}
                  >
                    {pred?.available ? (
                      <div className="mb-3 grid grid-cols-3 gap-3">
                        <Kpi label="Probability" value={pct(pred.congestion.probability)} hint={`warns at ≥ ${pct(pred.congestion.threshold)}`} />
                        <Kpi label="Predicted window" value={`t+${pred.congestion.window[0]}…t+${pred.congestion.window[1]} s`} />
                        <Kpi label="Network now" value={pred.congestion.currently_congested ? "Congested" : "Not congested"} />
                      </div>
                    ) : (
                      <p className="mb-3 text-sm text-slate-500">{pred?.available === false ? pred.reason : "No data"}</p>
                    )}
                    <CongestionChart data={points} showTruth={mode === "replay"} />
                    {mode === "replay" && (
                      <p className="mt-2 text-xs text-slate-500">
                        Red shading = congestion that actually happened in this recording (ground truth, shown for comparison only).
                        {gt?.future_label_available === false && " The last 30 s of a run have no 30-second ground truth."}
                      </p>
                    )}
                  </Card>

                  <Card title="Anomaly detection" subtitle="Isolation Forest score; an anomaly means unusual traffic, not proof of an attack">
                    {pred?.available ? (
                      <div className="mb-3 grid grid-cols-3 gap-3">
                        <Kpi label="Status" value={pred.anomaly.flagged ? "Unusual" : "Normal"} />
                        <Kpi label="Likely cause" value={pred.anomaly.kind ?? "—"} />
                        <Kpi label="Traffic type" value={pred.classification.label} hint={`${pct(pred.classification.probabilities[pred.classification.label])} confidence`} />
                      </div>
                    ) : (
                      <p className="mb-3 text-sm text-slate-500">{pred?.available === false ? pred.reason : "No data"}</p>
                    )}
                    {pred?.available && pred.anomaly.flagged && (
                      <div className="mb-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-900 ring-1 ring-amber-200">
                        <div className="font-semibold">Why this was flagged</div>
                        <ul className="ml-4 mt-1 list-disc">{pred.anomaly.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
                        {pred.anomaly.kind && advice[pred.anomaly.kind] && <p className="mt-2 italic">Suggested action: {advice[pred.anomaly.kind]}</p>}
                      </div>
                    )}
                    <AnomalyChart data={points} />
                  </Card>
                </div>

                <Card title="Alerts" subtitle={mode === "replay" ? "Raised so far in the recording (historical)" : "Raised for live samples"}>
                  {alerts.length === 0 ? (
                    <p className="text-sm text-slate-500">No alerts yet.</p>
                  ) : (
                    <ul className="divide-y divide-slate-100 text-sm">
                      {alerts.map((a, i) => (
                        <li key={`${a.second}-${a.kind}-${i}`} className="flex flex-wrap items-start gap-3 py-2">
                          <span className="w-16 tabular-nums text-slate-400">t={a.second}s</span>
                          <Pill className={SEVERITY_TONE[a.severity]}>{a.severity}</Pill>
                          <div>
                            <div className="font-medium">{a.title}</div>
                            <div className="text-xs text-slate-500">{a.explanation.join(" · ")}</div>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              </>
            )}
          </>
        )}
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-6 text-xs text-slate-400">
        NetGuard AI only analyses and recommends; it never changes the network configuration.
      </footer>
    </div>
  );
}
