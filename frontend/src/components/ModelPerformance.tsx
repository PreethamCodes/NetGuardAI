import type { Json, ModelsResponse } from "../types";
import { Card, Empty } from "./ui";

const f = (x: number | null | undefined, d = 3) => (x === null || x === undefined ? "n/a" : x.toFixed(d));

function Table({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-slate-200 text-slate-500">
            {head.map((h) => (
              <th key={h} className="px-2 py-1.5 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-slate-100 tabular-nums">
              {r.map((c, j) => (
                <td key={j} className={`px-2 py-1.5 ${j === 0 ? "font-medium text-slate-800" : "text-slate-600"}`}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ModelPerformance({ models }: { models: ModelsResponse | null }) {
  if (!models || !models.evaluation) return <Card title="Model performance"><Empty>No evaluation results found. Run <code>python -m netguard train</code>.</Empty></Card>;
  const ev: Json = models.evaluation;
  const cls = Object.entries<Json>(ev.classification.models).filter(([, m]) => m.test).map(([name, m]) => [
    name, f(m.test.accuracy), f(m.test.macro_precision), f(m.test.macro_recall), f(m.test.macro_f1), f(m.test.macro_pr_auc), f(m.resources.mean_ms, 2),
  ]);
  const anom = Object.entries<Json>(ev.anomaly.models).filter(([, m]) => m.test).map(([name, m]) => {
    const b = m.test.binary_vs_normal;
    return [name, f(b.precision), f(b.recall), f(b.f1), f(b.pr_auc), f(b.fpr), f(m.test.flag_rate_by_scenario.FLASH_CROWD), f(m.resources.mean_ms, 2)];
  });
  const cong = Object.entries<Json>(ev.congestion.methods).filter(([, m]) => m.test).map(([name, m]) => {
    const b = m.test.not_yet_congested;
    const lt = m.test.lead_time;
    return [
      `${name}${m.type === "baseline" ? " (baseline)" : ""}`, f(b.precision), f(b.recall), f(b.f1), f(b.pr_auc), f(b.fpr),
      `${lt.detected}/${lt.onsets}`, f(lt.mean_lead_s, 1), m.resources ? f(m.resources.mean_ms, 2) : "-",
    ];
  });
  return (
    <div className="grid gap-4">
      <Card title="Traffic classification" subtitle="Held-out test runs (one run per scenario). Macro-averaged over the 5 scenarios.">
        <Table head={["model", "accuracy", "precision", "recall", "F1", "PR-AUC", "latency ms"]} rows={cls} />
      </Card>
      <Card title="Anomaly detection" subtitle="Positive = any non-NORMAL second. FPR is measured on NORMAL; the FLASH_CROWD column is the share of that legitimate burst flagged (a false-positive risk).">
        <Table head={["model", "precision", "recall", "F1", "PR-AUC", "FPR (NORMAL)", "FLASH_CROWD flagged", "latency ms"]} rows={anom} />
      </Card>
      <Card title="Congestion prediction (next 30 s)" subtitle="Scored only on seconds that are not yet congested. 'Onsets' = congestion onsets warned about before they began, and the mean warning lead time.">
        <Table head={["method", "precision", "recall", "F1", "PR-AUC", "FPR", "onsets warned", "lead s", "latency ms"]} rows={cong} />
        <p className="mt-3 text-xs text-amber-700">
          Only a handful of congestion onsets exist in the test runs, so these results are tentative: they do not prove reliable 30-second-ahead prediction.
        </p>
      </Card>
    </div>
  );
}
