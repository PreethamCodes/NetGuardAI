"""Draw the system-architecture figure for the paper (Section 3.1).

Run from the repository root:  .venv/bin/python docs/paper/make_architecture.py
Output: docs/paper/figures/fig_architecture.png (300 dpi).
Every box names a component that exists in the repository; nothing here is simulated data.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

OUT = Path(__file__).resolve().parent / "figures"
OUT.mkdir(parents=True, exist_ok=True)
NAVY, TEAL, CORAL, GREY = "#0B1F3A", "#0B7F8A", "#E8604C", "#8A94A3"
plt.rcParams.update({"font.family": "DejaVu Serif", "figure.dpi": 150, "savefig.dpi": 300})

fig, ax = plt.subplots(figsize=(7.2, 4.5))
ax.set_xlim(0, 14.2)
ax.set_ylim(0, 9.0)
ax.axis("off")


def band(y, h, label, fc, top=True):
    ax.add_patch(Rectangle((0.0, y), 14.2, h, fc=fc, ec="none", zorder=0))
    ax.text(0.2, y + (h - 0.28 if top else 0.26), label, fontsize=7, weight="bold", color=GREY, va="center")


def box(x, y, w, h, title, body, ec=TEAL, fc="#EAF2F4", tc=TEAL):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.1", fc=fc, ec=ec, lw=0.9, zorder=2))
    ax.text(x + w / 2, y + h - 0.32, title, ha="center", va="center", fontsize=7.2, weight="bold", color=tc, zorder=3)
    ax.text(x + w / 2, y + (h - 0.55) / 2, body, ha="center", va="center", fontsize=5.8, color=NAVY, linespacing=1.3, zorder=3)


def arrow(p, q, color=NAVY, rad=0.0, lw=0.9, style="-|>"):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=8, lw=lw, color=color,
                                 connectionstyle=f"arc3,rad={rad}", zorder=4))


def tag(x, y, text, color=GREY, ha="center"):
    ax.text(x, y, text, ha=ha, va="center", fontsize=5.4, color=color, style="italic", zorder=5,
            bbox=dict(fc="white", ec="none", pad=0.6, alpha=0.9))


# ---- offline band: build and evaluate
band(4.75, 4.1, "OFFLINE: BUILD AND EVALUATE", "#F5F8FA")
w, g, y0, h = 2.45, 0.4, 5.1, 3.0
xs = [0.2 + i * (w + g) for i in range(5)]
box(xs[0], y0, w, h, "1  Network", "dumbbell, 8 hosts,\n2 switches\n10 Mbit/s bottleneck\n5 scripted scenarios")
box(xs[1], y0, w, h, "2  Measurement", "7 collectors\nsampled every 1 s\non one shared clock\n(null if a tool fails)")
box(xs[2], y0, w, h, "3  Dataset", "one row per second\ncongestion rule\nX = 29 ms\n30 s forward label\nvalidator")
box(xs[3], y0, w, h, "4  Learning", "105 past-only features\nsplit by whole run\nclassification, anomaly,\ncongestion prediction", ec=CORAL, fc="#FDF1EE", tc=CORAL)
box(xs[4], y0, w, h, "5  Models", "saved models and\nresult tables\n(evaluated on\nheld-out runs)", ec=CORAL, fc="#FDF1EE", tc=CORAL)
for i in range(4):
    arrow((xs[i] + w, y0 + h / 2), (xs[i + 1], y0 + h / 2))

# ---- online band: serve and show
band(0.1, 4.45, "ONLINE: SERVE AND SHOW", "#F5F8FA", top=False)
yb, hb = 0.75, 2.7
box(0.2, yb, 3.3, hb, "6  Live pipeline", "real scenario, same\nfeature code as offline\n4 s look-ahead so a flow's\nlast packet is known")
box(4.5, yb, 4.0, hb, "7  Backend (FastAPI)", "REST + WebSocket\nloads models, scores each second\nSQLite: alerts, live records\n'unavailable' if < 30 s history")
box(9.5, yb, 4.5, hb, "8  Dashboard (React)", "monitoring, alerts, model performance\n3D explanatory scenes\nattack-and-mitigation demo\n(advice only: never changes the network)")

arrow((3.5, yb + hb / 2), (4.5, yb + hb / 2))
tag(4.0, yb + hb / 2 + 0.42, "per-second\nmetrics")
arrow((8.5, yb + hb / 2), (9.5, yb + hb / 2))
tag(9.0, yb + hb / 2 + 0.42, "scores,\nwarnings")

# cross-band links
arrow((xs[4] + w / 2, y0), (7.6, yb + hb), color=CORAL, rad=0.0)
tag(10.8, 4.45, "load models", color=CORAL)
arrow((xs[0] + w / 2, y0), (1.85, yb + hb), color=GREY, rad=0.0)
tag(1.55, 4.45, "live run of a scenario", color=GREY)
arrow((xs[2] + w / 2, y0), (6.0, yb + hb), color=GREY, rad=0.0)
tag(6.0, 4.45, "recorded runs (replay)", color=GREY)

fig.savefig(OUT / "fig_architecture.png", bbox_inches="tight", facecolor="white")
plt.close(fig)
print("wrote", OUT / "fig_architecture.png")
