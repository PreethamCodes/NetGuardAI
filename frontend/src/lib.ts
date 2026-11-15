import type { MonitorRecord } from "./types";

export const num = (v: number | null | undefined, digits = 1, unit = ""): string =>
  v === null || v === undefined || Number.isNaN(v) ? "n/a" : `${v.toFixed(digits)}${unit}`;

export const pct = (v: number | null | undefined, digits = 0): string =>
  v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(digits)}%`;

export interface Point {
  t: number;
  throughput: number | null;
  utilisation: number | null;
  loss: number | null;
  probeLoss: number | null;
  rtt: number | null;
  jitter: number | null;
  queueDelay: number | null;
  connections: number | null;
  flows: number | null;
  newFlows: number | null;
  retrans: number | null;
  cpu: number | null;
  ram: number | null;
  anomalyScore: number | null;
  anomalyThreshold: number | null;
  congestionProb: number | null;
  congestionThreshold: number | null;
  trueCongested: boolean | null;
}

export function toPoint(r: MonitorRecord): Point {
  const m = r.metrics;
  const p = r.predictions;
  return {
    t: r.second,
    throughput: m.throughput_mbps ?? null,
    utilisation: m.utilisation_pct ?? null,
    loss: m.loss_pct ?? null,
    probeLoss: m.probe_loss_pct ?? null,
    rtt: m.rtt_mean_ms ?? null,
    jitter: m.jitter_ms ?? null,
    queueDelay: m.queue_delay_ms ?? null,
    connections: m.active_connections ?? null,
    flows: m.nf_active_flows ?? null,
    newFlows: m.nf_new_flows ?? null,
    retrans: m.tcp_retransmissions ?? null,
    cpu: m.cpu_percent ?? null,
    ram: m.ram_percent ?? null,
    anomalyScore: p.available ? p.anomaly.score : null,
    anomalyThreshold: p.available ? p.anomaly.threshold : null,
    congestionProb: p.available ? p.congestion.probability : null,
    congestionThreshold: p.available ? p.congestion.threshold : null,
    trueCongested: r.ground_truth?.current_congestion ?? null,
  };
}

/** Contiguous [start, end] second ranges where the recorded ground truth says "congested". */
export function congestedRanges(points: Point[]): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  let start: number | null = null;
  for (const p of points) {
    if (p.trueCongested) {
      if (start === null) start = p.t;
    } else if (start !== null) {
      out.push([start, p.t - 1]);
      start = null;
    }
  }
  if (start !== null && points.length) out.push([start, points[points.length - 1].t]);
  return out;
}

export type Condition = { label: string; tone: "ok" | "warn" | "bad" | "idle" };

export function condition(r: MonitorRecord | undefined): Condition {
  if (!r) return { label: "No data", tone: "idle" };
  const p = r.predictions;
  if (!p.available) return { label: "Warming up (needs 30 s of history)", tone: "idle" };
  if (p.congestion.currently_congested) return { label: "Congested", tone: "bad" };
  if (p.anomaly.flagged) return { label: `Unusual traffic: ${p.anomaly.kind ?? "unclassified"}`, tone: "warn" };
  return { label: "Normal", tone: "ok" };
}

export const TONE: Record<Condition["tone"], string> = {
  ok: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  warn: "bg-amber-100 text-amber-900 ring-amber-200",
  bad: "bg-red-100 text-red-800 ring-red-200",
  idle: "bg-slate-100 text-slate-600 ring-slate-200",
};

export const SEVERITY_TONE: Record<string, string> = {
  high: "bg-red-100 text-red-800",
  medium: "bg-amber-100 text-amber-900",
  low: "bg-sky-100 text-sky-800",
};
