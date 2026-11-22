"""Regenerate every figure and the feature-set ablation of the paper from the stored project data.

Run from the repository root:  PYTHONPATH=. .venv/bin/python docs/paper/make_figures.py
Nothing here is simulated: figures read data/features/features.parquet, models/*.joblib and results/ml_results.json.
"""
import json
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from netguard.ml.common import best_f1_threshold, binary_metrics, load_ml_data
from netguard.ml.congestion import lead_times

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "paper" / "figures"
OUT.mkdir(parents=True, exist_ok=True)
NAVY, TEAL, CORAL, GREY, AMBER = "#0B1F3A", "#0B7F8A", "#E8604C", "#8A94A3", "#E0A030"
plt.rcParams.update({"font.family": "DejaVu Serif", "font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.linewidth": 0.6, "figure.dpi": 150, "savefig.dpi": 300})

data = load_ml_data(ROOT / "data")
F = data.frame
res = json.load(open(ROOT / "results" / "ml_results.json"))


def box(ax, x, y, w, h, text, fc="#EAF2F4", ec=TEAL, fs=7, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=fc, ec=ec, lw=0.8))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=NAVY, weight="bold" if bold else "normal")


def arrow(ax, p, q, color=NAVY):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=7, lw=0.8, color=color))


# ---------------------------------------------------------------- Fig. 1 architecture
fig, ax = plt.subplots(figsize=(7.0, 2.35)); ax.set_xlim(0, 14); ax.set_ylim(0, 4.6); ax.axis("off")
stages = [("1  Simulate", "Mininet dumbbell\ntc netem + tbf\n5 scenarios"), ("2  Measure", "7 collectors\n1 s sampling\non one clock"),
          ("3  Label", "congestion rule\nX = 29 ms\n30 s future label"), ("4  Learn", "105 features\nsplit by run\n3 ML tasks"),
          ("5  Serve", "FastAPI +\nWebSocket\nlive ingest"), ("6  Show", "React dashboard\nadvice only")]
for i, (t, d) in enumerate(stages):
    x = 0.15 + i * 2.3
    box(ax, x, 2.55, 2.0, 1.55, "", fc="#EAF2F4")
    ax.text(x + 1.0, 3.78, t, ha="center", va="center", fontsize=7.5, weight="bold", color=TEAL)
    ax.text(x + 1.0, 3.0, d, ha="center", va="center", fontsize=5.8, color=NAVY, linespacing=1.25)
    if i < 5:
        arrow(ax, (x + 2.0, 3.3), (x + 2.3, 3.3))
box(ax, 0.15, 0.2, 4.2, 1.55, "", fc="#FDF1EE", ec=CORAL)
ax.text(2.25, 1.45, "Anomaly detection", ha="center", fontsize=7.2, weight="bold", color=CORAL)
ax.text(2.25, 0.8, "Isolation Forest (stage 1)\n+ rule-based explanation (stage 2)", ha="center", va="center", fontsize=6.3, color=NAVY)
box(ax, 4.9, 0.2, 4.2, 1.55, "", fc="#FDF1EE", ec=CORAL)
ax.text(7.0, 1.45, "Congestion prediction", ha="center", fontsize=7.2, weight="bold", color=CORAL)
ax.text(7.0, 0.8, "P(new congestion within 30 s)\nLogistic Regression vs. baselines", ha="center", va="center", fontsize=6.3, color=NAVY)
box(ax, 9.65, 0.2, 4.2, 1.55, "", fc="#FDF1EE", ec=CORAL)
ax.text(11.75, 1.45, "Traffic classification", ha="center", fontsize=7.2, weight="bold", color=CORAL)
ax.text(11.75, 0.8, "5 scenarios; XGBoost / RF / DT\n(diagnostic and reference task)", ha="center", va="center", fontsize=6.3, color=NAVY)
for x in (2.25, 7.0, 11.75):
    arrow(ax, (8.05, 2.55), (x, 1.75), color=GREY)
fig.savefig(OUT / "fig1_architecture.png", bbox_inches="tight"); plt.close(fig)

# ---------------------------------------------------------------- Fig. 2 topology
fig, ax = plt.subplots(figsize=(3.4, 2.15)); ax.set_xlim(0, 10); ax.set_ylim(0, 6.2); ax.axis("off")
for i in range(4):
    y = 5.2 - i * 1.4
    box(ax, 0.2, y - 0.3, 1.0, 0.6, f"h{i+1}", fs=6.5); box(ax, 8.8, y - 0.3, 1.0, 0.6, f"h{i+5}", fs=6.5)
    ax.plot([1.2, 3.1], [y, 3.1], color=GREY, lw=0.8); ax.plot([6.9, 8.8], [3.1, y], color=GREY, lw=0.8)
box(ax, 3.0, 2.7, 1.1, 0.8, "s1", fc="#E3E8F0", ec=NAVY, bold=True); box(ax, 5.9, 2.7, 1.1, 0.8, "s2", fc="#E3E8F0", ec=NAVY, bold=True)
ax.plot([4.1, 5.9], [3.1, 3.1], color=CORAL, lw=3)
ax.text(5.0, 3.55, "bottleneck", ha="center", fontsize=6.3, color=CORAL, weight="bold")
ax.text(5.0, 2.35, "10 Mbit/s, 20 ms\n50-pkt drop-tail queue", ha="center", va="top", fontsize=5.8, color=NAVY)
ax.text(1.7, 0.3, "access links 100 Mbit/s", ha="center", fontsize=5.8, color=GREY); ax.text(8.3, 0.3, "10.0.0.0/24", ha="center", fontsize=5.8, color=GREY)
fig.savefig(OUT / "fig2_topology.png", bbox_inches="tight"); plt.close(fig)

# ---------------------------------------------------------------- Fig. 3 test-run signals per scenario
order = ["NORMAL", "RAMP", "FLASH_CROWD", "UDP_FLOOD", "DEGRADED"]
fig, axes = plt.subplots(3, 5, figsize=(7.2, 3.9), sharex=True)
test = F[F["split"] == "test"]
for j, sc in enumerate(order):
    d = test[test["scenario_label"] == sc].sort_values("second")
    cur = d["current_congestion"].fillna(False).astype(bool).to_numpy()
    for i, (col, lab, c, lim) in enumerate([("utilisation_pct", "Utilisation (%)", NAVY, (0, 112)), ("queue_delay_ms", "Queue delay (ms)", TEAL, (0, 72)),
                                           ("rtt_mean_ms", "RTT (ms)", CORAL, (0, 130))]):
        a = axes[i, j]
        a.plot(d["second"], d[col], color=c, lw=0.6)
        a.fill_between(d["second"], lim[0], lim[1], where=cur, color=AMBER, alpha=0.30, lw=0, step="mid")
        a.set_ylim(*lim)
        if i == 0: a.set_title(sc.replace("_", " "), fontsize=7, weight="bold")
        if j == 0: a.set_ylabel(lab, fontsize=6.5)
        else: a.set_yticklabels([])
        a.tick_params(labelsize=5.5, length=2)
        if i == 2: a.set_xlabel("second", fontsize=6)
fig.tight_layout(pad=0.4); fig.savefig(OUT / "fig3_scenarios.png", bbox_inches="tight"); plt.close(fig)

# ---------------------------------------------------------------- Fig. 4 LR probability, held-out RAMP test run
lr = joblib.load(ROOT / "models" / "congestion_logistic_regression.joblib")["model"]
thr = res["congestion"]["methods"]["logistic_regression"]["test"]["threshold"]
fig, axes = plt.subplots(2, 1, figsize=(3.45, 3.0), sharex=True, gridspec_kw={"height_ratios": [1.2, 1]})
onset_info = {}
for sc, ls_ in (("RAMP", "-"),):
    d = F[(F["split"] == "test") & (F["scenario_label"] == sc)].sort_values("second")
    v = d[d["valid_for_prediction"].astype(bool)]
    p = lr.predict_proba(data.X(v, scaled=True))[:, 1]
    cur = d["current_congestion"].fillna(False).astype(bool).to_numpy()
    starts = np.flatnonzero(cur & ~np.r_[False, cur[:-1]])
    a = axes[0]
    a.plot(v["second"], p, color=NAVY, lw=0.9, label="P(congestion within 30 s)")
    a.axhline(thr, color=CORAL, ls="--", lw=0.8, label=f"threshold {thr:.2f} (from validation)")
    a.fill_between(d["second"], 0, 1, where=cur, color=AMBER, alpha=0.30, lw=0, step="mid", label="congested (ground truth)")
    for s in starts: a.axvline(s, color=TEAL, lw=0.7, ls=":")
    a.set_ylim(0, 1.02); a.set_ylabel("probability", fontsize=7)
    a.set_title("Held-out RAMP run (test split)", fontsize=7.5, weight="bold")
    b = axes[1]
    b.plot(d["second"], d["utilisation_pct"], color=NAVY, lw=0.8, label="utilisation (%)")
    b2 = b.twinx(); b2.plot(d["second"], d["queue_delay_ms"], color=TEAL, lw=0.8, label="queue delay (ms)"); b2.spines["right"].set_visible(True)
    b.set_ylabel("utilisation (%)", fontsize=7); b2.set_ylabel("queue delay (ms)", fontsize=7); b.set_xlabel("second", fontsize=7)
    b.fill_between(d["second"], 0, 110, where=cur, color=AMBER, alpha=0.30, lw=0, step="mid")
    for a_ in (a, b, b2): a_.tick_params(labelsize=6)
    onset_info[sc] = [int(s) for s in starts]
fig.tight_layout(pad=0.4); fig.savefig(OUT / "fig4_ramp_prediction.png", bbox_inches="tight"); plt.close(fig)

# ---------------------------------------------------------------- Fig. 5 F1 comparison (not-yet-congested, test)
meths = ["logistic_regression", "xgboost", "random_forest", "utilisation_gt_80", "utilisation_trend"]
names = ["Logistic\nRegression", "XGBoost", "Random\nForest", "Util. > 80 %", "Util. trend"]
f1 = [res["congestion"]["methods"][m]["test"]["not_yet_congested"]["f1"] for m in meths]
fig, ax = plt.subplots(figsize=(3.45, 2.0))
cols = [TEAL, TEAL, TEAL, GREY, GREY]
bars = ax.bar(names, f1, color=cols, width=0.62)
for b_, v_ in zip(bars, f1): ax.text(b_.get_x() + b_.get_width() / 2, v_ + 0.015, f"{v_:.2f}", ha="center", fontsize=7)
ax.set_ylabel("F1, not-yet-congested seconds", fontsize=7); ax.set_ylim(0, 0.8); ax.tick_params(labelsize=6.2)
ax.text(0.99, 0.97, "persistence: undefined\n(cannot warn before onset)", transform=ax.transAxes, ha="right", va="top", fontsize=5.8, color=GREY)
fig.tight_layout(pad=0.4); fig.savefig(OUT / "fig5_f1.png", bbox_inches="tight"); plt.close(fig)

# ---------------------------------------------------------------- Ablation (logistic regression, feature subsets)
base15 = data.columns[:15]
sets = {"5 raw signals": ["utilisation_pct", "queue_delay_ms", "loss_pct", "rtt_mean_ms", "throughput_mbps"],
        "15 instantaneous signals": list(base15),
        "Top-12 (ranked)": ["nf_active_flows_mean5", "share_udp", "loss_pct_std5", "rtt_mean_ms", "rtt_mean_ms_mean5", "rtt_mean_ms_lag30",
                            "nf_active_flows_max5", "share_dns", "active_connections_mean5", "nf_new_flows_mean5", "share_icmp", "rtt_mean_ms_lag5"],
        "All 105": list(data.columns)}
tr = F[(F["split"] == "train") & F["valid_for_prediction"].astype(bool)]
va = F[(F["split"] == "val") & F["valid_for_prediction"].astype(bool)]
te = F[(F["split"] == "test") & F["valid_for_prediction"].astype(bool)]
abl = {}
for name, cols_ in sets.items():
    sc = StandardScaler().fit(tr[cols_].to_numpy(float))
    m = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=0).fit(sc.transform(tr[cols_].to_numpy(float)), tr["future_congestion"].astype(int))
    pv = m.predict_proba(sc.transform(va[cols_].to_numpy(float)))[:, 1]
    t = best_f1_threshold(va["future_congestion"].astype(int).to_numpy(), pv)
    pt = m.predict_proba(sc.transform(te[cols_].to_numpy(float)))[:, 1]
    calm = ~te["current_congestion"].fillna(False).astype(bool).to_numpy()
    y = te["future_congestion"].astype(int).to_numpy()
    mt = binary_metrics(y[calm], (pt >= t).astype(int)[calm], pt[calm])
    lt = lead_times(te, (pt >= t).astype(int), F)
    abl[name] = {"n_features": len(cols_), "threshold": t, "precision": mt["precision"], "recall": mt["recall"], "f1": mt["f1"],
                 "pr_auc": mt["pr_auc"], "fpr": mt["fpr"], "onsets": f"{lt['detected']}/{lt['onsets']}"}
(ROOT / "docs" / "paper" / "ablation_results.json").write_text(json.dumps(abl, indent=2))
print(json.dumps(abl, indent=1)); print("RAMP onsets (test):", onset_info)

# per-scenario count facts used in the text
print(F[F.split == "test"].groupby("scenario_label")[["current_congestion"]].sum())
