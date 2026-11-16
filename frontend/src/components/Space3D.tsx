import { Fragment, useRef, useState } from "react";
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

type Scene = TopologyScene | ModelScene;

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

const SCENES: Record<string, Scene> = {
  "topo-experiment": TOPOLOGY_EXPERIMENT,
  "topo-attack": TOPOLOGY_ATTACK,
  "model-isoforest": MODEL_ISOFOREST,
  "model-logreg": MODEL_LOGREG,
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

// ---------------------------------------------------------------------------------------------- component
const GROUPS: { heading: string; items: { key: string; label: string }[] }[] = [
  { heading: "Network topology (2 best)", items: [{ key: "topo-experiment", label: "Experiment topology" }, { key: "topo-attack", label: "Live attack-demo topology" }] },
  { heading: "ML model (2 best)", items: [{ key: "model-isoforest", label: "Isolation Forest" }, { key: "model-logreg", label: "Logistic Regression" }] },
];

export function Space3D() {
  const [key, setKey] = useState<string>("topo-experiment");
  const [rot, setRot] = useState({ rx: -22, ry: -30 });
  const drag = useRef<{ x: number; y: number } | null>(null);

  const scene = SCENES[key];

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
    <Card title="3D view" subtitle="The project's network topology and ML models, as a draggable 3D space" right={<Pill className="bg-slate-100 text-slate-600 ring-1 ring-slate-300">drag to rotate</Pill>}>
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
          {scene.kind === "topology" ? <TopologyScene3D scene={scene} /> : <ModelScene3D scene={scene} />}
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
