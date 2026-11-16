# NetGuard AI

AI/ML-based network traffic analysis, anomaly detection and **30-second-ahead congestion prediction**, built and
evaluated on traffic generated in an emulated network (Mininet + Open vSwitch) with a FastAPI backend and a React
dashboard.

> Step-by-step commands are in **[docs/RUN_GUIDE.md](docs/RUN_GUIDE.md)**. This file explains what the system is, how the
> pieces fit together and what every file does. The original project design is in
> `NetGuard AI — Project Documentation.pdf`.

All traffic, including the attack-like UDP flood, exists **only between Mininet hosts inside the Ubuntu VM**. Nothing
here changes the network configuration of the machine or of the emulated network based on model output: the system only
analyses and recommends.

---------------------------------------------------------------------------------------------------------------------

## 1. What it does

| Goal | How it is done |
|---|---|
| Emulate a network | Mininet dumbbell topology, two Open vSwitch switches, a 10 Mbit/s / 20 ms bottleneck shaped with `tc` |
| Generate traffic | iperf3 (TCP/UDP), hping3, ping, a Python HTTP server/client, a Scapy DNS responder/client, driven by YAML scenarios |
| Measure | Once per second: `ip -s link`, `tc -s qdisc`, `ss -ti`, a 0.2 s ping probe, psutil, TShark and NFStream |
| Label | Ground-truth `scenario_label`, `current_congestion`, `future_congestion` (next 30 s), `time_to_congestion` |
| Learn | Traffic classification, anomaly detection (Isolation Forest + LOF + rules), 30 s congestion prediction |
| Serve | FastAPI + SQLite + WebSockets, historical replay and a real live feed |
| Show | React + TypeScript + Tailwind + Recharts dashboard |

```
                         ┌────────────────────────── Ubuntu VM (root) ──────────────────────────┐
 configs/*.yaml ───────▶ │  Mininet dumbbell  ──  traffic generators (iperf3, hping3, http, dns)│
 (topology, links,       │        │                                                              │
  scenarios, campaign)   │        ▼   1 s grid, shared clock                                     │
                         │  collectors: interface · queue(tc) · ss · ping probe · psutil ·       │
                         │              TShark pcap → NFStream flows                             │
                         └────────┬──────────────────────────────────────────┬──────────────────┘
                                  │ per-experiment Parquet + metadata        │ live: 1 JSON sample / s
                                  ▼                                          ▼
   labels (calibrated X) → data/processed → features (past only) → models/   POST /api/ingest
                                  │              │                   │               │
                                  │        data/features      results/ml_results     │
                                  ▼                                  ▼               ▼
                              replay  ──────────────▶  FastAPI backend (SQLite, WebSockets)
                                                                     │
                                                                     ▼
                                                         React dashboard (port 5173)
```

---------------------------------------------------------------------------------------------------------------------

## 2. Current results (real data, nothing simulated)

* **Dataset:** 15 successful experiments (5 scenarios × 3 repetitions × 300 s, seeds 42/43/44) = **4,500 one-second rows**.
  Two failed attempts (one interrupted, one iperf3 overrun) are on record and excluded. No run was invalid (CPU never hit 100 %
  inside a measurement window).
* **Congestion threshold X = 29 ms**, calibrated from the three NORMAL runs (99.9th percentile of queue delay on seconds with
  utilisation < 90 %, rounded up) and then frozen (`data/metadata/calibration.json`).
* **Features:** 105 per second (1 s + 5 s trailing windows, 5/10/30 s lags), split **by run**: repetition 1 → train,
  2 → validation, 3 → test (every scenario appears in every split).

Held-out **test** runs (one run per scenario):

| Task | Best result |
|---|---|
| Traffic classification (5 scenarios) | Random Forest accuracy 0.908, XGBoost 0.901, Decision Tree 0.893 |
| Anomaly detection (vs NORMAL) | Isolation Forest precision 0.97, recall 1.00, **false-positive rate on NORMAL 12.2 %**; flags **100 % of FLASH_CROWD** (legitimate traffic) |
| Congestion within 30 s, on seconds *not yet congested* | Logistic Regression precision 0.87, recall 0.52, F1 0.65, warned before 5 of 6 onsets (mean lead ≈ 30 s). Baselines: utilisation > 80 % F1 0.33, persistence cannot warn early |

**Read these honestly.** The test set has only 6 congestion onsets, from one run per scenario, so the 30-second prediction
result is *tentative evidence*, not proof of reliable prediction. Full tables: `results/ml_results.md`,
`results/prep_report.md`, `results/validation_report.md`.

**Live check:** a real live RAMP run (150 s) streamed 150 samples to the backend; its predictions matched the offline
pipeline for the same run on 100 % of seconds (mean probability difference 0.0012).

---------------------------------------------------------------------------------------------------------------------

## 3. How the pieces work (end to end)

### 3.1 Network (Phase 1)
`netguard/topology/dumbbell.py` builds `h1–h4 — s1 ═ bottleneck ═ s2 — h5–h8`. Switches are OVS in `standalone` mode
(learning switches, no controller). `netguard/topology/tc.py` shapes every interface with `netem` (delay/loss) + `tbf`
(rate and a 50-packet drop-tail queue). Shaping is read back from the kernel and compared with the configuration. `delay_ms`
is one-way and applied in both directions (≈ 40 ms RTT). The code refuses configurations where the access links are not
faster than the bottleneck.

### 3.2 Traffic (Phase 1)
A scenario YAML lists *components*; each has a *pattern* (NORMAL, BURST, PERIODIC, RANDOM, RAMP, FLASH_CROWD, UDP_FLOOD) that
`netguard/traffic/patterns.py` expands into a deterministic list of timed events (same scenario + seed ⇒ same events).
`netguard/traffic/executor.py` runs the events on Mininet hosts. Safety limits live in `configs/traffic.yaml`; hosts are
checked to be inside the private subnet before anything starts; hping3 is always bounded and rate-limited.

The five primary scenarios:

| Scenario | File | Purpose |
|---|---|---|
| NORMAL | `configs/scenarios/normal.yaml` | baseline mix of TCP, UDP, HTTP, DNS, ping (~35 % load) |
| RAMP | `ramp.yaml` | aggregate UDP load rises in steps and crosses capacity at ~60 s |
| FLASH_CROWD | `flash_crowd.yaml` | many legitimate TCP clients arrive within seconds |
| UDP_FLOOD | `udp_flood.yaml` | controlled high-rate UDP between Mininet hosts |
| DEGRADED | `degraded.yaml` | +60 ms delay and 3 % loss with normal load (`link_overrides`) |

(`burst`, `periodic`, `random`, `udp_flood_hping3` are extra Phase 1 scenarios, not part of the dataset.)

### 3.3 Collection (Phase 2)
`netguard/experiments/orchestrator.py` runs one experiment: clean Mininet → build and shape the network → start TShark → start
the collectors on a **shared one-second grid** (`timeline.py`) → run the traffic plan → stop everything → tear Mininet down and
verify the cleanup → derive per-second metrics, flows and labels → write Parquet + metadata. A run is `success`, `failed`
(missing collector output, failed traffic event, bad cleanup) or `invalid` (VM CPU reached 100 % during the measurement window).
Missing values stay `NaN`; nothing is filled or interpolated.

| Metric | Source | Collector file |
|---|---|---|
| throughput, pps, utilisation | `ip -s -j link` on both bottleneck ports | `collectors/interface.py` |
| queue backlog, delay, drops, loss | `tc -s -j qdisc` (tbf backlog ÷ link rate) | `collectors/queue.py` |
| RTT, jitter, probe loss | `ping -i 0.2` inside Mininet | `collectors/probe.py` |
| connections, TCP retransmissions | `ss -tinH` in every host namespace via `nsenter` | `collectors/connections.py` |
| CPU, RAM | psutil | `collectors/resources.py` |
| protocol counts | TShark header-only capture (96 B) | `collectors/capture.py` |
| flow records | NFStream on that pcap | `collectors/flows.py` |
| live protocol/flow counts | `tshark -l` stream | `collectors/livepackets.py` |

### 3.4 Labels (`netguard/experiments/labels.py`, `configs/labeling.yaml`)
A second is **congested** when utilisation ≥ 90 % **and** (queue delay ≥ X **or** loss > 1 %), holding for **≥ 3 consecutive seconds**.
Utilisation, queue delay and loss are the worse of the two bottleneck directions; loss is the `tc` drop percentage.
An **onset** is the first second of such a run.

* `current_congestion[t]` – t belongs to a congested run.
* `future_congestion[t]` – a *new, separate* onset occurs in `(t, t+30]` (a second already inside an episode asks about the *next* episode).
* `time_to_congestion[t]` – seconds to that next onset (**evaluation only, never a model input**).
* The last 30 s of each run have **no** `future_congestion` (`future_label_available = False`); unmeasured seconds give null labels.

**X calibration:** `python -m netguard experiment calibrate` (also automatic after the NORMAL runs) pools NORMAL seconds with
utilisation < 90 %, takes the 99.9th percentile of queue delay, rounds up to whole ms (floor 1 ms) and writes
`data/metadata/calibration.json`. It refuses to overwrite unless `--force`.

### 3.5 Features (Phase 3, `netguard/prep/`)
`python -m netguard prepare` validates every run (status, duplicates, gap-free 1 s timestamps, labels recomputed and compared),
excludes bad runs, builds features **for one run at a time**, assigns splits by run and fits a `StandardScaler` on training
rows only.

* 1 s signals + trailing 5 s **mean/max/std/slope**; **5/10/30 s lags** for the key signals; protocol shares; SYN ratio;
  `congested_causal` (a past-only stand-in for the label, because the true `current_congestion` needs the next 2 s).
* First 30 s of each run have incomplete lags → `NaN`, flagged `features_complete = False`; never filled.
* `valid_for_prediction` = complete features **and** a valid 30 s label.
* Labels, IDs and VM CPU/RAM are never model inputs.

### 3.6 Models (Phase 4/5, `netguard/ml/`) — `python -m netguard train`
* **Classification** – Decision Tree, Random Forest, XGBoost predict the scenario.
* **Anomaly detection** – Isolation Forest (primary) and LOF fitted on NORMAL training rows only; threshold = 95th percentile of
  NORMAL *validation* scores. A rule layer (`AnomalyInterpreter`) then names the likely cause (congestion, flood-like,
  legitimate burst, degradation) with the numbers that triggered it. An anomaly score alone does **not** prove an attack.
* **Congestion prediction** – Logistic Regression, Random Forest, XGBoost vs baselines (persistence, utilisation > 80 %,
  rising-utilisation trend), thresholds chosen on validation runs, scored also on *not-yet-congested* seconds, with onsets
  warned and warning lead time. Latency, CPU time, memory and model size are measured.

### 3.7 Backend (`netguard/backend/`) — `python -m netguard serve`
Loads `models/*.joblib` and serves the recorded runs and live data. Every record is labelled `source: "replay"` (historical)
or `source: "live"`. Predictions are withheld (`available: false`) until 30 s of history exist.
Stores live records and alerts in SQLite (`data/backend/netguard.db`).

### 3.8 Live mode (`python -m netguard live <scenario>`)
Runs a **real** Mininet scenario and, once per second, posts the measurements to `POST /api/ingest`
(`experiments/livefeed.py`). The live reader (`livepackets.py`) reproduces the offline TShark/NFStream signals; each second is
posted ~4 s after it ends because the flow definition needs each flow's last packet. After the run the experiment is also
saved and the live values are compared with the offline ones (`checks.live_vs_offline` in its metadata).

### 3.9 Dashboard (`frontend/`)
**Historical replay** (the 15 recorded runs; held-out `test` runs are marked as unseen by the models) and **Live monitoring**
(real-time stream, or a *past live session* played back). Panels: overview, throughput/utilisation, latency/queue/loss,
flows and connections, protocol mix, congestion probability with the 30 s window, anomaly score with the reason it was flagged,
alerts, and a Model performance tab with the real held-out metrics. Banners always say whether data is historical or live.

---------------------------------------------------------------------------------------------------------------------

## 4. Repository map (what every file does)

### Root
| Path | Purpose |
|---|---|
| `README.md`, `docs/RUN_GUIDE.md` | this explanation and the step-by-step commands |
| `requirements.txt` | pinned Python dependencies (Phase 1–6) |
| `pytest.ini` | tests config; integration tests are opt-in (`-m integration`) |
| `.gitignore` | excludes `.venv`, `data/*` (except calibration), `models/`, `logs/`, `node_modules/` |
| `NetGuard AI — Project Documentation.pdf` | original design document |

### `configs/` – everything is configuration driven
| File | Purpose |
|---|---|
| `topology.yaml` | hosts per side (4), subnet `10.0.0.0/24`, switch names, OVS fail mode |
| `links.yaml` | bottleneck (10 Mbit/s, 20 ms, 50-packet queue) and access (100 Mbit/s) links |
| `traffic.yaml` | iperf3/HTTP/DNS/ping/hping3 settings and safety limits |
| `scenarios/*.yaml` | one file per scenario (see §3.2) |
| `labeling.yaml` | congestion definition (90 %, 1 %, 3 s, 30 s horizon) and the X calibration method |
| `experiments/phase2.yaml` | the campaign: 5 scenarios × 3 repetitions, seeds, collectors, capture and validation options |

### `netguard/` – the Python package
| Module | Purpose |
|---|---|
| `__main__.py`, `cli.py` | the `python -m netguard <command>` entry point |
| `exceptions.py` | `NetGuardError`, `ConfigError`, `TopologyError`, `TrafficError` |
| `config/` | typed, validated models and YAML loader for topology, links, traffic (`environment.py`, `loader.py`) |
| `utils/` | `validation.py` (YAML checking helpers), `system.py` (pre-flight checks), `logging_setup.py` |
| `topology/dumbbell.py`, `tc.py` | the Mininet dumbbell; `tc` netem + tbf command builders and parsers |
| `scenarios/` | scenario models (`models.py`), per-pattern options (`options.py`), loader, one-shot runner |
| `traffic/` | `patterns.py` + `planner.py` (pure, seeded planning), `commands.py` (argv builders, iperf3 JSON parsing), `executor.py` (runs a plan on Mininet hosts), `events.py`, `types.py` |
| `traffic/workers/` | Python HTTP and DNS servers/clients run inside Mininet hosts |
| `experiments/config.py` | campaign and labelling configuration models |
| `experiments/orchestrator.py` | runs experiments, statuses, resume, auto-calibration |
| `experiments/timeline.py` | the shared 1 s clock |
| `experiments/lifecycle.py` | clean start/end of Mininet and OVS (`mn -c` equivalent) with verification |
| `experiments/collectors/*` | the collectors listed in §3.3 (`base.py` has the common thread/health logic) |
| `experiments/labels.py` | ground-truth congestion labels (pure functions) |
| `experiments/calibration.py`, `postprocess.py` | X calibration; relabelling stored runs |
| `experiments/dataset.py` | per-second table assembly, Parquet writer, metadata, schema validation, `dataset build` |
| `experiments/report.py` | `results/validation_report.md` |
| `experiments/livefeed.py` | per-second live samples → backend; live-vs-offline comparison |
| `experiments/cli.py` | `experiment …`, `live`, `dataset …` commands |
| `prep/` | `features.py`, `validate.py`, `splits.py`, `pipeline.py`, `cli.py` (Phase 3) |
| `ml/` | `common.py` (data loading, metrics, resource profile), `classification.py`, `anomaly.py`, `congestion.py`, `run.py`, `cli.py` |
| `backend/` | `app.py` (REST + WebSockets), `inference.py`, `replay.py`, `live.py`, `db.py` (SQLite), `cli.py` |

### `frontend/`
`src/App.tsx` (layout, state, playback, live socket), `api.ts` (backend client), `types.ts`, `lib.ts` (helpers),
`components/Charts.tsx`, `ModelPerformance.tsx`, `ui.tsx`; `vite.config.ts` proxies `/api` and `/ws` to port 8000.

### `tests/`
`unit/` (≈ 450 tests, no root needed) and `integration/` (real Mininet, root, opt-in). The unit tests use small synthetic
tables only to check code logic; they are never used as experimental data.

### Generated (mostly not committed)
`data/raw|processed|metadata|features|live|backend`, `models/*.joblib`, `results/*.md|json`, `logs/`.

---------------------------------------------------------------------------------------------------------------------

## 5. Data layout and schemas

```
data/metadata/<experiment_id>.json   config, seeds, timings, status, checks, errors   (+ calibration.json, tracked)
data/raw/<experiment_id>/            interface|queue|connections|resources|probe .parquet, flows.parquet, protocol_hierarchy.txt
data/processed/<experiment_id>.parquet   one row per second: features of the measurements + labels
data/processed/dataset.parquet           successful runs only (python -m netguard dataset build)
data/features/features.parquet           model input table with `split`, plus scaler.joblib and feature_columns.json
data/live/                           real live runs (same layout)           data/backend/netguard.db   alerts + live records
models/                              classification_*, anomaly_*, congestion_*  .joblib
```

Per-second table columns (selection): identifiers (`experiment_id`, `scenario`, `repetition`, `seed`, `second`, `timestamp_s`,
`experiment_status`); interface (`fwd_/rev_` throughput, pps, utilisation); queue (`*_queue_backlog_bytes/packets`,
`*_queue_delay_ms`, `*_queue_drops_per_s`, `*_tc_loss_pct`); probe (`rtt_mean_ms`, `jitter_ms`, `probe_loss_pct`); `active_connections`,
`tcp_retransmissions`; `cpu_percent`, `ram_percent`; `tshark_*` protocol counts; `nf_active_flows`, `nf_new_flows`; summaries
`throughput_mbps`, `utilisation_pct`, `queue_delay_ms`, `loss_pct`; labels `scenario_label`, `current_congestion`,
`future_congestion`, `time_to_congestion`, `future_label_available`, `labels_status`.

Utilisation uses `ip -s link` bytes, which include the 14-byte Ethernet header, so a saturated 10 Mbit/s link reads about 100.9 %.

---------------------------------------------------------------------------------------------------------------------

## 6. Backend API

| Method and path | Returns |
|---|---|
| `GET /api/health` | status, models in use, number of replay runs |
| `GET /api/models` | held-out evaluation results of every model and baseline |
| `GET /api/data-summary` | the Phase 3 preparation report |
| `GET /api/runs` | the 15 recorded runs (scenario, split, `seen_by_models`, `historical: true`) |
| `GET /api/runs/{id}/timeline` | every second: metrics, predictions, ground truth (separate) and alerts |
| `GET /api/runs/{id}/summary` | counts of flagged seconds, warnings and alerts |
| `WS  /ws/replay/{id}?speed=10` | streams a recorded run, each message marked historical |
| `POST /api/ingest` | one live sample (missing metrics = `null`); returns predictions and alerts |
| `GET /api/live/records`, `/api/live/streams` | stored live samples and the list of past live sessions |
| `WS  /ws/live` | pushes each ingested live record |
| `DELETE /api/live/{stream_id}` | resets a live stream's history |
| `GET /api/alerts?source=live|replay` | stored alerts |
| `GET /api/recommendations` | suggested actions per cause (advice only) |

Interactive documentation: `http://127.0.0.1:8000/docs`.

---------------------------------------------------------------------------------------------------------------------

## 7. Design decisions and limitations

* **Why a custom dataset:** the 30-second congestion target needs queue delay, utilisation and a known bottleneck, which public
  intrusion datasets (UNSW-NB15, CICIDS2017) do not contain. They remain optional supplementary benchmarks.
* **Anomaly detection flags FLASH_CROWD.** Stage 1 flags 100 % of that legitimate burst on the test run; the rule layer only
  partly separates it (rule agreement with the true scenario ≈ 50 %, RAMP is explained as "flood-like" because it is also UDP).
* **Few congestion onsets.** 15 runs give about 6 onsets per split, so prediction metrics are tentative.
* **Loss in the congestion label** is the `tc` drop percentage (queue overflow plus netem random loss); probe loss is stored
  but too coarse (5 probes/s) to label with. This is a modelling choice, documented in `labels.py`.
* **Live mode lags ≈ 4 s** (flow look-ahead) and needs the models' 30 s history before predicting.
* **Live flow counts** use the offline NFStream definition; SYN counts attribute SYNs to the second they are seen rather than
  the flow start second. The live-vs-offline report quantifies the difference per run.
* **One VM, one topology, one bottleneck.** Results do not claim to generalise to other networks.
* **Ubuntu's AppArmor profile for tshark** cannot enter other users' directories and rejects signals from other processes, so the capture is written to a
  root-owned temp folder and bounded with `-a duration`.
* The VM is assumed dedicated: Mininet cleanup (`mn -c`) removes **all** Open vSwitch bridges.

---------------------------------------------------------------------------------------------------------------------

## 8. Live attack demo (attack a sample website, watch it get detected)

A lightweight, two-part demo built entirely from this repo's own code: a real website hosted inside the
test network (it serves a hardcoded `503` error page for the flood's own window, t=120s–180s, so the
failure is always clearly visible), a real UDP flood against it, and the project's own trained models
(XGBoost classifier, Isolation Forest anomaly detector, Logistic Regression congestion predictor)
scoring it live. Full walkthrough, timing table and talking points:
[`docs/DEMO_GUIDE.md`](docs/DEMO_GUIDE.md).

With the backend already running (`python -m netguard serve`):

```bash
# 1. Part 1 - show the site fail, undefended (no mitigation):
sudo .venv/bin/python scripts/demo_attack.py --duration 200
```

```bash
# 2. Part 2 - show it detected AND mitigated: the moment the model flags the flood as
#    flood-like/congestion, the attacker's link is rate-limited for real with `tc`, and the
#    site should recover while the flood is still running:
sudo .venv/bin/python scripts/demo_attack.py --duration 200 --mitigate
```

```bash
# 3. In a second terminal, for either run - watch the website's real health live:
tail -f /tmp/netguard_demo/site_probe.log
```

If the main frontend (section 6) is running, open `http://localhost:5173` and click its **"Live attack
demo"** tab - the same dashboard, embedded, so every page of this project (monitoring, model
performance, the 3D view, and the attack demo) lives behind one address. Otherwise open
[`demo/dashboard.html`](demo/dashboard.html) directly in a browser (no build step needed; it connects to
the backend's `/ws/live` WebSocket either way).
