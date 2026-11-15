import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { Point } from "../lib";
import { congestedRanges } from "../lib";
import type { MonitorRecord } from "../types";

const AXIS = { fontSize: 11, fill: "#64748b" };

function Frame({ height = 220, children }: { height?: number; children: React.ReactElement }) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

export function ThroughputChart({ data }: { data: Point[] }) {
  return (
    <Frame>
      <LineChart data={data} margin={{ left: -10, right: 10, top: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="t" tick={AXIS} label={{ value: "second", position: "insideBottomRight", offset: -2, fontSize: 11 }} />
        <YAxis yAxisId="mbps" tick={AXIS} />
        <YAxis yAxisId="pct" orientation="right" domain={[0, 110]} tick={AXIS} />
        <Tooltip />
        <Legend />
        <Line yAxisId="mbps" type="monotone" dataKey="throughput" name="Throughput (Mbps)" stroke="#2563eb" dot={false} strokeWidth={2} isAnimationActive={false} />
        <Line yAxisId="pct" type="monotone" dataKey="utilisation" name="Utilisation (%)" stroke="#f97316" dot={false} strokeWidth={2} isAnimationActive={false} />
        <ReferenceLine yAxisId="pct" y={90} stroke="#ef4444" strokeDasharray="4 4" label={{ value: "90 %", fontSize: 10, fill: "#ef4444" }} />
      </LineChart>
    </Frame>
  );
}

export function LatencyChart({ data }: { data: Point[] }) {
  return (
    <Frame>
      <LineChart data={data} margin={{ left: -10, right: 10, top: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="t" tick={AXIS} />
        <YAxis tick={AXIS} />
        <Tooltip />
        <Legend />
        <Line type="monotone" dataKey="rtt" name="RTT (ms)" stroke="#7c3aed" dot={false} strokeWidth={2} isAnimationActive={false} />
        <Line type="monotone" dataKey="jitter" name="Jitter (ms)" stroke="#0891b2" dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="queueDelay" name="Queue delay (ms)" stroke="#dc2626" dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="loss" name="Loss (%)" stroke="#16a34a" dot={false} isAnimationActive={false} />
      </LineChart>
    </Frame>
  );
}

export function FlowsChart({ data }: { data: Point[] }) {
  return (
    <Frame>
      <LineChart data={data} margin={{ left: -10, right: 10, top: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="t" tick={AXIS} />
        <YAxis tick={AXIS} />
        <Tooltip />
        <Legend />
        <Line type="monotone" dataKey="flows" name="Active flows" stroke="#2563eb" dot={false} strokeWidth={2} isAnimationActive={false} />
        <Line type="monotone" dataKey="newFlows" name="New flows / s" stroke="#f59e0b" dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="connections" name="Active connections" stroke="#16a34a" dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="retrans" name="TCP retransmissions / s" stroke="#dc2626" dot={false} isAnimationActive={false} />
      </LineChart>
    </Frame>
  );
}

export function ResourceChart({ data }: { data: Point[] }) {
  return (
    <Frame height={160}>
      <LineChart data={data} margin={{ left: -10, right: 10, top: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="t" tick={AXIS} />
        <YAxis domain={[0, 100]} tick={AXIS} />
        <Tooltip />
        <Legend />
        <Line type="monotone" dataKey="cpu" name="VM CPU (%)" stroke="#475569" dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="ram" name="VM RAM (%)" stroke="#94a3b8" dot={false} isAnimationActive={false} />
      </LineChart>
    </Frame>
  );
}

export function ProtocolChart({ record }: { record: MonitorRecord | undefined }) {
  const m = record?.metrics ?? {};
  const rows = [
    { name: "TCP", pkts: m.tshark_tcp_pkts ?? 0 },
    { name: "UDP", pkts: m.tshark_udp_pkts ?? 0 },
    { name: "ICMP", pkts: m.tshark_icmp_pkts ?? 0 },
    { name: "DNS (in UDP)", pkts: m.tshark_dns_pkts ?? 0 },
    { name: "HTTP (in TCP)", pkts: m.tshark_http_pkts ?? 0 },
  ];
  return (
    <Frame height={190}>
      <BarChart data={rows} layout="vertical" margin={{ left: 30, right: 10 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis type="number" tick={AXIS} />
        <YAxis type="category" dataKey="name" tick={AXIS} width={90} />
        <Tooltip />
        <Bar dataKey="pkts" name="packets in this second" fill="#2563eb" isAnimationActive={false} />
      </BarChart>
    </Frame>
  );
}

export function AnomalyChart({ data }: { data: Point[] }) {
  const thr = data.find((d) => d.anomalyThreshold !== null)?.anomalyThreshold ?? undefined;
  return (
    <Frame height={200}>
      <LineChart data={data} margin={{ left: -10, right: 10, top: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="t" tick={AXIS} />
        <YAxis tick={AXIS} domain={["auto", "auto"]} />
        <Tooltip formatter={(v) => (typeof v === "number" ? v.toFixed(3) : String(v))} />
        <Line type="monotone" dataKey="anomalyScore" name="Anomaly score (higher = more unusual)" stroke="#dc2626" dot={false} strokeWidth={2} isAnimationActive={false} />
        {thr !== undefined && <ReferenceLine y={thr} stroke="#0f172a" strokeDasharray="4 4" label={{ value: "flag threshold", fontSize: 10 }} />}
        <Legend />
      </LineChart>
    </Frame>
  );
}

export function CongestionChart({ data, showTruth }: { data: Point[]; showTruth: boolean }) {
  const thr = data.find((d) => d.congestionThreshold !== null)?.congestionThreshold ?? undefined;
  const ranges = showTruth ? congestedRanges(data) : [];
  return (
    <Frame height={220}>
      <LineChart data={data} margin={{ left: -10, right: 10, top: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="t" tick={AXIS} />
        <YAxis domain={[0, 1]} tick={AXIS} />
        <Tooltip formatter={(v) => (typeof v === "number" ? v.toFixed(2) : String(v))} />
        {ranges.map(([a, b]) => (
          <ReferenceArea key={`${a}-${b}`} x1={a} x2={b} fill="#ef4444" fillOpacity={0.12} />
        ))}
        <Line type="monotone" dataKey="congestionProb" name="P(congestion within 30 s)" stroke="#2563eb" dot={false} strokeWidth={2} isAnimationActive={false} />
        {thr !== undefined && <ReferenceLine y={thr} stroke="#dc2626" strokeDasharray="4 4" label={{ value: "warning threshold", fontSize: 10, fill: "#dc2626" }} />}
        <Legend />
      </LineChart>
    </Frame>
  );
}
