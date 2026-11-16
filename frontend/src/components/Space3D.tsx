import { Fragment, useEffect, useRef, useState } from "react";
import { Card, Pill } from "./ui";

/**
 * A small, dependency-free 3D space (pure CSS 3D transforms, no three.js) showing the project's
 * network topology in its two most-used roles, and the two best-performing ML models as simple
 * illustrative diagrams. Drag to rotate. Deliberately simple: flat 2D diagrams (edges are plain
 * rotated lines) that sit inside a draggable 3D world, with a few nodes/points "popped" forward
 * on the Z axis for emphasis - no per-edge 3D trigonometry needed.
 */

// ---------------------------------------------------------------------------------------------- types
type TopoNode = { id: string; x: number; y: number; z?: number; size: number; color: string; label: string };
type TopoEdge = { from: string; to: string; color: string; width?: number };
type TopologyScene = { kind: "topology"; title: string; caption: string; nodes: TopoNode[]; edges: TopoEdge[] };

type Point = { id: string; x: number; y: number; z: number; size: number; color: string };
type ModelScene = {
  kind: "model";
  title: string;
  caption: string;
  points: Point[];
  ring?: { r: number };
  wall?: { color: string };
  legend: { label: string; color: string }[];
};

type SimScene = {
  kind: "queue-sim" | "attack-sim" | "classify-sim" | "congestion-sim" | "syn-sim";
  title: string;
  caption: string;
};

type Scene = TopologyScene | ModelScene | SimScene;

// ---------------------------------------------------------------------------------------------- data
// Shared node layout for both topology scenes: dumbbell, h1-h4 <-> s1 == bottleneck == s2 <-> h5-h8.
const SWITCH = { size: 26 };
const HOST_Y = [-150, -50, 50, 150];

function dumbbellNodes(roles: Record<string, { color: string; label: string; pop?: boolean }>, dim: string): TopoNode[] {
  const base: TopoNode[] = [
    { id: "s1", x: -70, y: 0, size: SWITCH.size, color: "#334155", label: "s1" },
    { id: "s2", x: 70, y: 0, size: SWITCH.size, color: "#334155", label: "s2" },
    ...["h1", "h2", "h3", "h4"].map((id, i) => ({ id, x: -260, y: HOST_Y[i], size: 16, color: dim, label: id })),
    ...["h5", "h6", "h7", "h8"].map((id, i) => ({ id, x: 260, y: HOST_Y[i], size: 16, color: dim, label: id })),
  ];
  return base.map((n) => (roles[n.id] ? { ...n, color: roles[n.id].color, label: roles[n.id].label, z: roles[n.id].pop ? 70 : undefined } : n));
}

const ACCESS = "#cbd5e1"; // light slate - undramatic default link
function accessEdges(overrides: Record<string, string> = {}): TopoEdge[] {
  const hosts = ["h1", "h2", "h3", "h4", "h5", "h6", "h7", "h8"];
  return hosts.map((h) => ({ from: h, to: Number(h.slice(1)) <= 4 ? "s1" : "s2", color: overrides[h] ?? ACCESS, width: 2 }));
}

const TOPOLOGY_EXPERIMENT: TopologyScene = {
  kind: "topology",
  title: "Experiment topology",
  caption:
    "The dumbbell network used to generate every second of the training data: h1-h4 and h5-h8, two Open vSwitch " +
    "switches, a shared 10 Mbit/s bottleneck (20 ms one-way delay, a 50-packet queue). The same topology runs all " +
    "five scenarios (NORMAL, RAMP, FLASH_CROWD, UDP_FLOOD, DEGRADED).",
  nodes: dumbbellNodes({}, "#38bdf8"),
  edges: [{ from: "s1", to: "s2", color: "#4f46e5", width: 5 }, ...accessEdges()],
};

const TOPOLOGY_ATTACK: TopologyScene = {
  kind: "topology",
  title: "Live attack-demo topology",
  caption:
    "The same physical network, with roles assigned for the live attack demo: h1 floods (red), h5 hosts the demo " +
    "website (amber, the flood's target), h2 probes the website every second (green). The bottleneck (red) is " +
    "driven toward saturation while the rest of the hosts sit idle - shown dim.",
  nodes: dumbbellNodes(
    {
      h1: { color: "#dc2626", label: "h1 (attacker)", pop: true },
      h5: { color: "#f59e0b", label: "h5 (website)", pop: true },
      h2: { color: "#10b981", label: "h2 (prober)", pop: true },
    },
    "#94a3b8",
  ),
  edges: [
    { from: "s1", to: "s2", color: "#dc2626", width: 6 },
    ...accessEdges({ h1: "#f87171", h5: "#fbbf24", h2: "#34d399" }),
  ],
};

// Illustrative point clouds - fixed coordinates, not literal model internals.
const MODEL_ISOFOREST: ModelScene = {
  kind: "model",
  title: "Isolation Forest — anomaly detector",
  caption:
    "Illustrative, not the literal trained trees: a second is an outlier when it is easy to isolate from the rest - " +
    "points far from the normal cluster need fewer random splits to separate, so they pop outside the boundary. " +
    "Trained only on NORMAL traffic. On held-out test seconds: precision 0.97, recall 1.00, but ~12% false alarms " +
    "on NORMAL and it flags 100% of the legitimate FLASH_CROWD scenario.",
  points: [
    ...[
      [-40, -20], [20, -35], [50, 10], [-10, 40], [30, 35], [-55, 15], [5, -5], [-25, 30], [45, -15], [10, 20],
    ].map(([x, y], i) => ({ id: `n${i}`, x, y, z: 0, size: 12, color: "#34d399" })),
    ...[
      [150, -70, 60], [-170, 40, 80], [60, 150, 50], [-90, -150, 70], [170, 80, 40], [-150, -60, 90],
    ].map(([x, y, z], i) => ({ id: `a${i}`, x, y, z, size: 16, color: "#f87171" })),
  ],
  ring: { r: 95 },
  legend: [{ label: "normal second", color: "#34d399" }, { label: "flagged anomaly", color: "#f87171" }],
};

const MODEL_LOGREG: ModelScene = {
  kind: "model",
  title: "Logistic Regression — congestion predictor",
  caption:
    "Illustrative over 2 of the 105 real features, not the literal 105-dimensional boundary: the model fits a " +
    "straight-line boundary; a case on the right crosses probability 0.65 (the chosen threshold) and is predicted " +
    "to congest within 30 s. On held-out test runs: precision 0.87, recall 0.52, F1 0.65, warned before 5 of the " +
    "6 held-out onsets, about 30 s early.",
  points: [
    ...[
      [-160, -120, 20], [-120, 40, -30], [-180, 100, 10], [-90, -60, 40], [-150, 10, -20], [-100, 150, 0],
      [-170, -20, 30], [-60, -140, -10],
    ].map(([x, y, z], i) => ({ id: `b${i}`, x, y, z, size: 13, color: "#60a5fa" })),
    ...[
      [160, -110, -20], [120, 50, 30], [180, 110, -10], [90, -50, -40], [150, 20, 20], [100, 140, 0],
      [170, -30, -30], [60, -130, 10],
    ].map(([x, y, z], i) => ({ id: `c${i}`, x, y, z, size: 13, color: "#f59e0b" })),
  ],
  wall: { color: "rgba(99,102,241,0.28)" },
  legend: [{ label: "no congestion predicted", color: "#60a5fa" }, { label: "congestion predicted (≥ 65%)", color: "#f59e0b" }],
};

const QUEUE_SIM: SimScene = {
  kind: "queue-sim",
  title: "Queue & packet drop (live simulation)",
  caption:
    "Illustrative, not real traffic: a simplified version of our real 50-packet drop-tail queue (the `tbf` on " +
    "the bottleneck link), scaled down to 10 slots here. Watch it fill during a burst and overflow - packets " +
    "dropped, shown in red - once full. This is the same drop-tail / bufferbloat mechanism explained in " +
    "docs/cn_concepts/03-queueing-and-congestion.md, where a full 50-packet queue adds up to ~60 ms of delay.",
};

const ATTACK_SIM: SimScene = {
  kind: "attack-sim",
  title: "Attack & mitigation (live simulation)",
  caption:
    "Illustrative, not live network traffic: start the attack to see flood packets (red) crowd the bottleneck " +
    "toward the website (h5); turn on mitigation to see them throttled right at the attacker's own link (h1) - " +
    "exactly what `scripts/demo_attack.py --mitigate` does for real. The legitimate prober (green, h2) keeps " +
    "flowing throughout, unaffected either way.",
};

const CLASSIFY_SIM: SimScene = {
  kind: "classify-sim",
  title: "Traffic classification (live simulation)",
  caption:
    "Illustrative, not real traffic: each second is labelled by its true scenario, sent into the model, and comes " +
    "out the other side as a prediction. Our real XGBoost classifier gets this right ~90% of the time on data it " +
    "never saw - so every so often (as it does here) a RAMP second is predicted as UDP_FLOOD instead, the one " +
    "confusion our own results document (both are mostly fast UDP traffic, see ../ml-explanations/01-models.md).",
};

const CONGESTION_TIMELINE_SIM: SimScene = {
  kind: "congestion-sim",
  title: "Congestion building up (live simulation)",
  caption:
    "Illustrative, not real traffic, but the exact real rule: utilisation ≥ 90% AND (queue delay ≥ ~29 ms OR " +
    "loss > 1%), sustained for 3 seconds. Watch utilisation rise first, queue delay follow once the link is full, " +
    "and loss only appear once the queue overflows - the same chain reaction as the real RAMP run in " +
    "../ml-explanations/02-features.md, where second 62 is exactly where this rule first fires.",
};

const SYN_SIM: SimScene = {
  kind: "syn-sim",
  title: "TCP handshake vs. SYN flood (live simulation)",
  caption:
    "Illustrative, not real traffic: a normal connection completes a 3-way handshake (SYN → SYN-ACK → ACK) in " +
    "full; a SYN flood sends only the first SYN, again and again, from one source, piling up half-open " +
    "connections the server is still waiting on. This is exactly what our `syn_ratio` feature (one of the 105) " +
    "is built to catch - see docs/cn_concepts/02-switching-and-transport.md.",
};

const SCENES: Record<string, Scene> = {
  "topo-experiment": TOPOLOGY_EXPERIMENT,
  "topo-attack": TOPOLOGY_ATTACK,
  "model-isoforest": MODEL_ISOFOREST,
  "model-logreg": MODEL_LOGREG,
  "sim-queue": QUEUE_SIM,
  "sim-attack": ATTACK_SIM,
  "sim-classify": CLASSIFY_SIM,
  "sim-congestion": CONGESTION_TIMELINE_SIM,
  "sim-syn": SYN_SIM,
};

// ---------------------------------------------------------------------------------------------- 3D primitives
const CX = 320; // stage center, px
const CY = 190;

function FlatEdge({ x1, y1, x2, y2, color, width = 2 }: { x1: number; y1: number; x2: number; y2: number; color: string; width?: number }) {
  const dx = x2 - x1, dy = y2 - y1;
  const length = Math.hypot(dx, dy);
  const angle = (Math.atan2(dy, dx) * 180) / Math.PI;
  return (
    <div
      style={{
        position: "absolute", left: 0, top: 0, width: length, height: width, background: color,
        transformOrigin: "0 50%", transform: `translate3d(${x1}px, ${y1}px, 0px) rotateZ(${angle}deg)`,
        borderRadius: width,
      }}
    />
  );
}

function Stem({ x, y, z, color }: { x: number; y: number; z: number; color: string }) {
  // a vertical post from the node's base (z=0) up to its popped position (0,0,z) - a fixed rotateY(90deg), no trig needed.
  return (
    <div
      style={{
        position: "absolute", left: 0, top: 0, width: Math.abs(z), height: 2, background: color, opacity: 0.5,
        transformOrigin: "0 50%", transform: `translate3d(${x}px, ${y}px, 0px) rotateY(90deg)`,
      }}
    />
  );
}

function Dot({ x, y, z = 0, size, color, label, labelBelow }: { x: number; y: number; z?: number; size: number; color: string; label?: string; labelBelow?: boolean }) {
  return (
    <div style={{ position: "absolute", left: 0, top: 0, transform: `translate3d(${x - size / 2}px, ${y - size / 2}px, ${z}px)` }}>
      <div style={{ width: size, height: size, borderRadius: "50%", background: color, boxShadow: `0 0 10px ${color}99` }} />
      {label && (
        <div
          style={{ position: "absolute", left: "50%", top: labelBelow ? size + 3 : -16, transform: "translateX(-50%)", whiteSpace: "nowrap" }}
          className="text-[10px] font-medium text-slate-200"
        >
          {label}
        </div>
      )}
    </div>
  );
}

function TopologyScene3D({ scene }: { scene: TopologyScene }) {
  const byId = Object.fromEntries(scene.nodes.map((n) => [n.id, n]));
  return (
    <>
      {scene.edges.map((e) => {
        const a = byId[e.from], b = byId[e.to];
        if (!a || !b) return null;
        return <FlatEdge key={`${e.from}-${e.to}`} x1={CX + a.x} y1={CY + a.y} x2={CX + b.x} y2={CY + b.y} color={e.color} width={e.width} />;
      })}
      {scene.nodes.map((n) => (
        <Fragment key={n.id}>
          {n.z && <Stem x={CX + n.x} y={CY + n.y} z={n.z} color={n.color} />}
          <Dot x={CX + n.x} y={CY + n.y} z={n.z ?? 0} size={n.size} color={n.color} label={n.label} labelBelow={n.id.startsWith("s")} />
        </Fragment>
      ))}
    </>
  );
}

function ModelScene3D({ scene }: { scene: ModelScene }) {
  return (
    <>
      {scene.ring && (
        <div
          style={{
            position: "absolute", left: 0, top: 0, width: scene.ring.r * 2, height: scene.ring.r * 2,
            borderRadius: "50%", border: "2px dashed #475569",
            transform: `translate3d(${CX - scene.ring.r}px, ${CY - scene.ring.r}px, 0px)`,
          }}
        />
      )}
      {scene.wall && (
        <div
          style={{
            position: "absolute", left: 0, top: 0, width: 260, height: 260, background: scene.wall.color,
            transformOrigin: "0 50%", transform: `translate3d(${CX}px, ${CY - 130}px, -130px) rotateY(90deg)`,
          }}
        />
      )}
      {scene.points.map((p) => (
        <Dot key={p.id} x={CX + p.x} y={CY + p.y} z={p.z} size={p.size} color={p.color} />
      ))}
    </>
  );
}

// --------------------------------------------------------------------------------------- moving dots (sims)
type Pt = { x: number; y: number; z?: number };

/** Position at `progress` (0..1) along a multi-point path, split proportionally by segment length. */
function pointAlongPath(path: Pt[], progress: number): Pt {
  const segs = path.slice(1).map((p, i) => Math.hypot(p.x - path[i].x, p.y - path[i].y));
  const total = segs.reduce((a, b) => a + b, 0) || 1;
  let dist = Math.max(0, Math.min(1, progress)) * total;
  for (let i = 0; i < segs.length; i++) {
    const segLen = segs[i] || 0.0001;
    if (dist <= segLen || i === segs.length - 1) {
      const t = Math.min(1, dist / segLen);
      const a = path[i], b = path[i + 1];
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: (a.z ?? 0) + ((b.z ?? 0) - (a.z ?? 0)) * t };
    }
    dist -= segLen;
  }
  return path[path.length - 1];
}

/** N packets flowing continuously along `path`; `cutoff` (0..1) stops them early (e.g. mitigated at the source). */
function FlowingDots({ path, count, tick, speed, size, color, cutoff = 1 }: {
  path: Pt[]; count: number; tick: number; speed: number; size: number; color: string; cutoff?: number;
}) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => {
        const progress = ((tick * speed + i / count) % 1 + 1) % 1;
        if (progress > cutoff) return null;
        const p = pointAlongPath(path, progress);
        return <Dot key={i} x={CX + p.x} y={CY + p.y} z={p.z ?? 0} size={size} color={color} />;
      })}
    </>
  );
}

const QUEUE_CYCLE = 110; // ticks per fill/drain loop
function queuePhase(tick: number): { occupancy: number; dropping: boolean } {
  const t = tick % QUEUE_CYCLE;
  if (t < 40) return { occupancy: Math.min(3, t / 10), dropping: false }; // quiet: arrivals < service rate
  if (t < 90) { // burst: arrivals > service rate -> fills up and overflows
    const occ = Math.min(10, 3 + (t - 40) * 0.3);
    return { occupancy: occ, dropping: occ >= 10 };
  }
  return { occupancy: Math.max(0, 10 - (t - 90) * 0.5), dropping: false }; // drains back down
}

function QueueScene3D({ tick }: { tick: number }) {
  const { occupancy, dropping } = queuePhase(tick);
  const slots = 10;
  const filled = Math.round(occupancy);
  const delayMs = Math.round((occupancy / slots) * 60);
  const inPath: Pt[] = [{ x: -220, y: 0 }, { x: -60, y: 0 }];
  const outPath: Pt[] = [{ x: 60, y: 0 }, { x: 220, y: 0 }];
  const color = occupancy >= 9 ? "#f87171" : occupancy >= 6 ? "#fbbf24" : "#34d399";
  return (
    <>
      <FlatEdge x1={CX - 220} y1={CY} x2={CX - 60} y2={CY} color="#475569" width={4} />
      <FlatEdge x1={CX + 60} y1={CY} x2={CX + 220} y2={CY} color="#475569" width={4} />
      <FlowingDots path={inPath} count={3} tick={tick} speed={0.02} size={10} color="#60a5fa" />
      {queuePhase(tick).occupancy > 0 && (
        <FlowingDots path={outPath} count={2} tick={tick} speed={0.03} size={10} color="#34d399" />
      )}
      {/* the queue "tower": 10 stacked slots, filled from the bottom */}
      {Array.from({ length: slots }, (_, i) => (
        <div
          key={i}
          style={{
            position: "absolute", left: 0, top: 0, width: 44, height: 16,
            transform: `translate3d(${CX - 22}px, ${CY + 90 - i * 19}px, 0px)`,
            background: i < filled ? color : "#1e293b", border: "1px solid #334155", borderRadius: 3,
          }}
        />
      ))}
      <Dot x={CX} y={CY - 110} size={1} color="transparent" label={`queue: ${filled}/${slots} slots (~${delayMs} ms)`} />
      {dropping && <Dot x={CX + 40} y={CY - 90} size={14} color="#ef4444" label="packet dropped!" />}
    </>
  );
}

function AttackScene3D({ tick, attacking, mitigating }: { tick: number; attacking: boolean; mitigating: boolean }) {
  const s1: Pt = { x: -70, y: 0 }, s2: Pt = { x: 70, y: 0 };
  const h1: Pt = { x: -260, y: -150 }, h2: Pt = { x: -260, y: -50 }, h5: Pt = { x: 260, y: -150 };
  const attackPath = [h1, s1, s2, h5];
  const legitPath = [h2, s1, s2, h5];
  const blocked = attacking && mitigating;
  return (
    <>
      <FlatEdge x1={CX + h1.x} y1={CY + h1.y} x2={CX + s1.x} y2={CY + s1.y} color={attacking ? "#f87171" : "#cbd5e1"} width={3} />
      <FlatEdge x1={CX + h2.x} y1={CY + h2.y} x2={CX + s1.x} y2={CY + s1.y} color="#34d399" width={2} />
      <FlatEdge x1={CX + s1.x} y1={CY + s1.y} x2={CX + s2.x} y2={CY + s2.y} color={attacking && !blocked ? "#dc2626" : "#4f46e5"} width={attacking && !blocked ? 7 : 5} />
      <FlatEdge x1={CX + s2.x} y1={CY + s2.y} x2={CX + h5.x} y2={CY + h5.y} color={attacking && !blocked ? "#f59e0b" : "#cbd5e1"} width={2} />
      <Dot x={CX + s1.x} y={CY + s1.y} size={26} color="#334155" label="s1" labelBelow />
      <Dot x={CX + s2.x} y={CY + s2.y} size={26} color="#334155" label="s2" labelBelow />
      <Dot x={CX + h1.x} y={CY + h1.y} size={18} color="#dc2626" label="h1 (attacker)" />
      <Dot x={CX + h2.x} y={CY + h2.y} size={16} color="#34d399" label="h2 (prober)" />
      <Dot x={CX + h5.x} y={CY + h5.y} size={18} color="#f59e0b" label="h5 (website)" />
      {blocked && <Dot x={CX + h1.x + 50} y={CY + h1.y + 10} size={16} color="#fbbf24" label="🛡 throttled here" />}
      {attacking && <FlowingDots path={attackPath} count={6} tick={tick} speed={0.03} size={10} color="#ef4444" cutoff={blocked ? 0.16 : 1} />}
      <FlowingDots path={legitPath} count={2} tick={tick} speed={0.015} size={9} color="#6ee7b7" />
    </>
  );
}

// ---------------------------------------------------------------------------------- 3 more simulations
const CLASS_COLORS: Record<string, string> = {
  NORMAL: "#38bdf8", RAMP: "#fbbf24", FLASH_CROWD: "#34d399", UDP_FLOOD: "#f87171", DEGRADED: "#a78bfa",
};
const CLASS_BINS = ["NORMAL", "RAMP", "FLASH_CROWD", "UDP_FLOOD", "DEGRADED"];
// A fixed, repeating sequence of "true" scenario labels; one entry (a later RAMP) is deliberately
// mis-predicted as UDP_FLOOD - the one real confusion our classifier's own results document.
const CLASS_SEQUENCE = ["NORMAL", "RAMP", "UDP_FLOOD", "FLASH_CROWD", "DEGRADED", "RAMP", "NORMAL", "UDP_FLOOD"];
const CLASS_MISPREDICT_INDEX = 5; // the second RAMP -> predicted UDP_FLOOD

function ClassifyScene3D({ tick }: { tick: number }) {
  const CYCLE = 70; // 35 ticks input->model, 35 ticks model->bin
  const idx = Math.floor(tick / CYCLE) % CLASS_SEQUENCE.length;
  const phase = tick % CYCLE;
  const trueLabel = CLASS_SEQUENCE[idx];
  const predicted = idx === CLASS_MISPREDICT_INDEX ? "UDP_FLOOD" : trueLabel;
  const mismatch = predicted !== trueLabel;

  const inPt: Pt = { x: -260, y: 0 };
  const modelPt: Pt = { x: -40, y: 0 };
  const binY = [-140, -70, 0, 70, 140];
  const bins: Record<string, Pt> = Object.fromEntries(CLASS_BINS.map((b, i) => [b, { x: 220, y: binY[i] }]));

  const traveling = phase < 35;
  const progress = traveling ? phase / 35 : (phase - 35) / 35;
  const path = traveling ? [inPt, modelPt] : [modelPt, bins[predicted]];
  const dotColor = traveling ? CLASS_COLORS[trueLabel] : CLASS_COLORS[predicted];
  const pos = pointAlongPath(path, progress);
  const justArrived = !traveling && phase > 60;

  return (
    <>
      <FlatEdge x1={CX + inPt.x} y1={CY + inPt.y} x2={CX + modelPt.x} y2={CY + modelPt.y} color="#475569" width={3} />
      {CLASS_BINS.map((b, i) => (
        <FlatEdge key={b} x1={CX + modelPt.x} y1={CY + modelPt.y} x2={CX + bins[b].x} y2={CY + binY[i]} color="#334155" width={2} />
      ))}
      <Dot x={CX + inPt.x} y={CY + inPt.y} size={16} color="#94a3b8" label="incoming second" labelBelow />
      <Dot x={CX + modelPt.x} y={CY + modelPt.y} size={34} color="#1d4ed8" label="model (XGBoost)" labelBelow />
      {CLASS_BINS.map((b, i) => (
        <Dot key={b} x={CX + bins[b].x} y={CY + binY[i]} size={18}
          color={justArrived && predicted === b ? CLASS_COLORS[b] : "#1e293b"} label={b} />
      ))}
      <Dot x={CX + pos.x} y={CY + pos.y} size={14} color={dotColor} />
      <Dot
        x={CX} y={CY - 150} size={1} color="transparent"
        label={`true: ${trueLabel}   →   predicted: ${predicted}${mismatch ? "  ✗ mismatch!" : "  ✓"}`}
      />
    </>
  );
}

const CONG_CYCLE = 140;
function congestionPhase(tick: number) {
  const t = tick % CONG_CYCLE;
  const util = t < 50 ? (t / 50) * 100 : t < 110 ? 100 : Math.max(0, 100 - (t - 110) * 3.3);
  const queueDelay = t < 45 ? 0 : t < 70 ? ((t - 45) / 25) * 60 : t < 110 ? 60 : Math.max(0, 60 - (t - 110) * 2);
  const loss = t < 65 ? 0 : t < 80 ? ((t - 65) / 15) * 25 : t < 110 ? 25 : Math.max(0, 25 - (t - 110) * 1);
  const congested = util >= 90 && (queueDelay >= 29 || loss > 1);
  return { util, queueDelay, loss, congested };
}

function TimelineBar({ x, label, value, max, unit, color }: { x: number; label: string; value: number; max: number; unit: string; color: string }) {
  const h = Math.max(2, (value / max) * 180);
  return (
    <>
      <div style={{ position: "absolute", left: 0, top: 0, width: 50, height: 180, border: "1px solid #334155", borderRadius: 4, transform: `translate3d(${CX + x - 25}px, ${CY - 90}px, 0px)` }} />
      <div style={{ position: "absolute", left: 0, top: 0, width: 50, height: h, background: color, borderRadius: 4, transform: `translate3d(${CX + x - 25}px, ${CY + 90 - h}px, 0px)` }} />
      <Dot x={CX + x} y={CY + 100} size={1} color="transparent" label={label} labelBelow />
      <Dot x={CX + x} y={CY - 100} size={1} color="transparent" label={`${value.toFixed(0)}${unit}`} />
    </>
  );
}

function CongestionTimelineScene3D({ tick }: { tick: number }) {
  const { util, queueDelay, loss, congested } = congestionPhase(tick);
  return (
    <>
      <TimelineBar x={-180} label="utilisation %" value={util} max={100} unit="%" color={util >= 90 ? "#f87171" : "#38bdf8"} />
      <TimelineBar x={-60} label="queue delay ms" value={queueDelay} max={60} unit="ms" color={queueDelay >= 29 ? "#fbbf24" : "#60a5fa"} />
      <TimelineBar x={60} label="loss %" value={loss} max={25} unit="%" color={loss > 1 ? "#f87171" : "#60a5fa"} />
      <Dot
        x={CX + 200} y={CY} size={34} color={congested ? "#dc2626" : "#10b981"}
        label={congested ? "⚠ CONGESTED" : "normal"}
      />
    </>
  );
}

const SYN_CYCLE = 160;
function SynFloodScene3D({ tick }: { tick: number }) {
  const t = tick % SYN_CYCLE;
  const flooding = t >= 80;
  const client: Pt = { x: -170, y: 0 }, server: Pt = { x: 170, y: 0 };

  // Normal phase: a repeating handshake every 24 ticks (SYN 0-8, SYN-ACK 8-16, ACK 16-24).
  const hsT = t % 24;
  const established = Math.floor(t / 24);

  // Flood phase: continuous one-way SYNs only, half-open count grows the longer it runs.
  const halfOpen = flooding ? Math.min(200, Math.round((t - 80) * 2.5)) : 0;

  return (
    <>
      <FlatEdge x1={CX + client.x} y1={CY + client.y} x2={CX + server.x} y2={CY + server.y} color={flooding ? "#f87171" : "#475569"} width={flooding ? 5 : 2} />
      <Dot x={CX + client.x} y={CY + client.y} size={22} color="#38bdf8" label="client" labelBelow />
      <Dot x={CX + server.x} y={CY + server.y} size={22} color="#334155" label="server" labelBelow />

      {!flooding && (
        <>
          {hsT < 8 && <Dot x={CX + client.x + (hsT / 8) * (server.x - client.x)} y={CY} size={11} color="#34d399" label="SYN" />}
          {hsT >= 8 && hsT < 16 && <Dot x={CX + server.x - ((hsT - 8) / 8) * (server.x - client.x)} y={CY} size={11} color="#fbbf24" label="SYN-ACK" />}
          {hsT >= 16 && hsT < 24 && <Dot x={CX + client.x + ((hsT - 16) / 8) * (server.x - client.x)} y={CY} size={11} color="#34d399" label="ACK" />}
          <Dot x={CX} y={CY - 90} size={1} color="transparent" label={`connections established: ${established}`} />
        </>
      )}

      {flooding && (
        <>
          <FlowingDots path={[client, server]} count={8} tick={tick} speed={0.08} size={10} color="#ef4444" />
          <Dot x={CX} y={CY - 90} size={1} color="transparent" label={`half-open connections: ${halfOpen}`} />
          {halfOpen > 50 && (
            <Dot x={CX} y={CY - 130} size={16} color="#dc2626" label="⚠ SYN flood detected (syn_ratio high)" />
          )}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------------------------- component
const GROUPS: { heading: string; items: { key: string; label: string }[] }[] = [
  { heading: "Network topology (2 best)", items: [{ key: "topo-experiment", label: "Experiment topology" }, { key: "topo-attack", label: "Live attack-demo topology" }] },
  { heading: "ML model (2 best)", items: [{ key: "model-isoforest", label: "Isolation Forest" }, { key: "model-logreg", label: "Logistic Regression" }] },
  { heading: "Live simulations (5)", items: [
    { key: "sim-queue", label: "Queue & packet drop" },
    { key: "sim-attack", label: "Attack & mitigation" },
    { key: "sim-classify", label: "Traffic classification" },
    { key: "sim-congestion", label: "Congestion building up" },
    { key: "sim-syn", label: "Handshake vs. SYN flood" },
  ] },
];

const SIM_KEYS = new Set(["sim-queue", "sim-attack", "sim-classify", "sim-congestion", "sim-syn"]);

export function Space3D() {
  const [key, setKey] = useState<string>("topo-experiment");
  const [rot, setRot] = useState({ rx: -22, ry: -30 });
  const [tick, setTick] = useState(0);
  const [attacking, setAttacking] = useState(false);
  const [mitigating, setMitigating] = useState(false);
  const drag = useRef<{ x: number; y: number } | null>(null);

  const scene = SCENES[key];

  useEffect(() => {
    if (!SIM_KEYS.has(key)) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 60);
    return () => window.clearInterval(id);
  }, [key]);

  function onDown(e: React.PointerEvent) {
    drag.current = { x: e.clientX, y: e.clientY };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }
  function onMove(e: React.PointerEvent) {
    if (!drag.current) return;
    const dx = e.clientX - drag.current.x, dy = e.clientY - drag.current.y;
    drag.current = { x: e.clientX, y: e.clientY };
    setRot((r) => ({ rx: Math.max(-80, Math.min(80, r.rx - dy * 0.4)), ry: r.ry + dx * 0.4 }));
  }
  function onUp() {
    drag.current = null;
  }

  return (
    <Card title="3D view" subtitle="Network topology, ML models, and 5 live simulations - all in a draggable 3D space" right={<Pill className="bg-slate-100 text-slate-600 ring-1 ring-slate-300">drag to rotate</Pill>}>
      <div className="mb-3 flex flex-wrap gap-4">
        {GROUPS.map((g) => (
          <div key={g.heading}>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{g.heading}</div>
            <div className="flex gap-1 rounded-lg bg-slate-100 p-1 text-sm">
              {g.items.map((it) => (
                <button
                  key={it.key}
                  onClick={() => setKey(it.key)}
                  className={`rounded-md px-3 py-1 ${key === it.key ? "bg-blue-600 text-white" : "text-slate-600 hover:text-slate-900"}`}
                >
                  {it.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {scene.kind === "attack-sim" && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <button
            onClick={() => setAttacking((a) => !a)}
            className={`rounded-md px-3 py-1 text-sm font-medium ${attacking ? "bg-red-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
          >
            {attacking ? "⏸ Stop attack" : "▶ Start attack"}
          </button>
          <button
            onClick={() => setMitigating((m) => !m)}
            disabled={!attacking}
            className={`rounded-md px-3 py-1 text-sm font-medium disabled:opacity-40 ${mitigating ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
          >
            🛡 {mitigating ? "Mitigation ON" : "Mitigate"}
          </button>
          <Pill className={
            !attacking ? "bg-emerald-100 text-emerald-700 ring-1 ring-emerald-300"
              : mitigating ? "bg-amber-100 text-amber-700 ring-1 ring-amber-300"
                : "bg-red-100 text-red-700 ring-1 ring-red-300"
          }>
            website h5: {!attacking ? "🟢 healthy" : mitigating ? "🟡 defended (mitigated)" : "🔴 overloaded (503)"}
          </Pill>
        </div>
      )}

      <div
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={onUp}
        className="relative h-[420px] w-full touch-none select-none overflow-hidden rounded-xl bg-gradient-to-b from-slate-900 to-slate-800"
        style={{ cursor: "grab", perspective: 900 }}
      >
        <div
          className="absolute left-0 top-0 h-full w-full"
          style={{ transformStyle: "preserve-3d", transform: `rotateX(${rot.rx}deg) rotateY(${rot.ry}deg)` }}
        >
          {scene.kind === "topology" && <TopologyScene3D scene={scene} />}
          {scene.kind === "model" && <ModelScene3D scene={scene} />}
          {scene.kind === "queue-sim" && <QueueScene3D tick={tick} />}
          {scene.kind === "attack-sim" && <AttackScene3D tick={tick} attacking={attacking} mitigating={mitigating} />}
          {scene.kind === "classify-sim" && <ClassifyScene3D tick={tick} />}
          {scene.kind === "congestion-sim" && <CongestionTimelineScene3D tick={tick} />}
          {scene.kind === "syn-sim" && <SynFloodScene3D tick={tick} />}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        {scene.kind === "model" && scene.legend.map((l) => (
          <span key={l.label} className="flex items-center gap-1.5 text-xs text-slate-600">
            <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: l.color }} />
            {l.label}
          </span>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-500">{scene.caption}</p>
    </Card>
  );
}
