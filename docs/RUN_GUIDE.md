# NetGuard AI — Run Guide

Step-by-step instructions, from a fresh Ubuntu VM to a full live demo. For what everything means see the [README](../README.md).
Commands marked **(sudo)** need root because Mininet and packet capture do; everything else runs as your normal user.
Project folder used below: `~/Desktop/netguardAI/NetGuardAI`.

---------------------------------------------------------------------------------------------------------------------

## 0. Quick paths

| I want to… | Do this |
|---|---|
| **See the dashboard with the data already recorded** | §3 (no root, ~2 min) |
| **Run a plain live network demo** | §3 then §7 |
| **Attack a sample website and watch it get detected + fixed** | §3 then §8 |
| **Explain the CN/ML concepts visually, no backend needed** | §9 (3D view tab) |
| **Give the whole class/viva demo, start to finish** | §10 |
| **Regenerate everything from scratch** | §1 → §4 → §5 → §6 → §3 |
| **Only check the code** | §11 (unit tests, no root) |

---------------------------------------------------------------------------------------------------------------------

## 1. One-time setup (Ubuntu VM)

Recommended VM: 6–8 vCPUs, 8 GB RAM, ~20 GB free disk, Ubuntu 22.04 or newer (developed on a newer release with Python 3.14).

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf3 hping3 iproute2 ethtool tshark util-linux \
                    python3-venv python3-pip nodejs npm
sudo systemctl enable --now openvswitch-switch
sudo mn --test pingall           # sanity check of Mininet + OVS; must show 0% dropped
```

Python environment (the venv must see the apt-installed `mininet` module, hence `--system-site-packages`):

```bash
cd ~/Desktop/netguardAI/NetGuardAI
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

Dashboard dependencies:

```bash
cd frontend && npm install && cd ..
```

Check the installation (no root, about 2 minutes):

```bash
.venv/bin/python -m netguard validate        # validates all YAML files and plans every scenario
.venv/bin/python -m pytest -q                # unit tests
```

---------------------------------------------------------------------------------------------------------------------

## 2. What gets created where

| Step | Command | Creates |
|---|---|---|
| Campaign | `experiment run` | `data/metadata/`, `data/processed/`, `data/raw/`, `results/validation_report.md` |
| Calibration | automatic / `experiment calibrate` | `data/metadata/calibration.json` (committed; X is fixed) |
| Preparation | `prepare` | `data/features/features.parquet`, `scaler.joblib`, `feature_columns.json`, `results/prep_report.md` |
| Training | `train` | `models/*.joblib`, `results/ml_results.md` and `.json` |
| Backend | `serve` | `data/backend/netguard.db` (alerts, live records) |
| Live demo | `live` | `data/live/…` (the live run saved as an experiment) |
| Attack demo | `scripts/demo_attack.py` | `/tmp/netguard_demo/` (website, probe/mitigation logs) + a live stream stored in `netguard.db` |

`data/` (except the calibration file), `models/`, `logs/`, `node_modules/` and `/tmp/netguard_demo/` are not committed.
Anyone can regenerate them with the steps below.

---------------------------------------------------------------------------------------------------------------------

## 3. Start the dashboard (replay of recorded data)

Needs `data/features/` and `models/` (created by §5 and §6). Open **two terminals**.

**Terminal 1 – backend** (port 8000; loads the models, takes a few seconds):

```bash
cd ~/Desktop/netguardAI/NetGuardAI
.venv/bin/python -m netguard serve --port 8000
```

Wait for `Uvicorn running on http://127.0.0.1:8000`. A 404 at `/` is normal (API only). Try
`http://127.0.0.1:8000/docs` and `http://127.0.0.1:8000/api/health`.

**Terminal 2 – dashboard** (port 5173):

```bash
cd ~/Desktop/netguardAI/NetGuardAI/frontend
npm run dev
```

Open **http://127.0.0.1:5173**. The top nav has four tabs:

1. **Monitoring** — historical replay or live monitoring (see below).
2. **Model performance** — the real held-out metrics of every model and baseline, with the caveats.
3. **3D view** — topologies, models and 5 live simulations, no backend data needed (§9).
4. **Live attack demo** — the attack-detection dashboard, embedded (§8).

How to use the Monitoring tab:
1. **Historical replay** (amber banner): pick a run in the dropdown. Runs labelled **test** were never seen by the models.
   Press **Play** (try 10×) or drag the slider. Try the **RAMP** test run at second ~58: congestion probability ≈ 95 % and a
   warning appears before congestion starts at second 60.
2. Overview cards, throughput/utilisation, latency/queue/loss, flows and connections, protocol mix, congestion prediction
   (red shading = congestion that really happened, for comparison only), anomaly score with the reason it was flagged and
   a suggested action, alerts.
3. **Live monitoring:** see §7.

Stop a server with `Ctrl-C` in its terminal. After retraining the models, restart the backend.

If the port is taken (`address already in use`): a backend is already running — that's fine, reuse it (check
`curl http://127.0.0.1:8000/api/health`), or `pkill -f "netguard serve"` and start again.

---------------------------------------------------------------------------------------------------------------------

## 4. Generate the dataset from scratch (Phase 1–2)

### 4.1 Look before you run (no root)
```bash
.venv/bin/python -m netguard list                              # available scenarios
.venv/bin/python -m netguard plan ramp --events | head -30     # dry run: the planned traffic, no network needed
.venv/bin/python -m netguard experiment run --dry-run          # the 15 planned runs and their seeds
```

### 4.2 One short real experiment first (sudo, ~75 s)
Use a **separate output folder** so the test run does not count as a campaign run:

```bash
cat > /tmp/smoke.yaml <<'YAML'
name: smoke
scenarios: [normal]
repetitions: 1
seeds: [42]
output: {data_dir: /tmp/netguard-smoke/data, results_dir: /tmp/netguard-smoke/results}
YAML
sudo .venv/bin/python -m netguard experiment run /tmp/smoke.yaml --duration 45
```
It prints `<experiment id>: success` and writes Parquet and metadata under `/tmp/netguard-smoke/data`. Labels stay
`pending_calibration` (a 45 s run is too short to calibrate X). Do not continue to the full campaign until this succeeds.

### 4.3 The full campaign (sudo, ≈ 90–100 min, one command)
```bash
sudo .venv/bin/python -m netguard experiment run configs/experiments/phase2.yaml
```
* Runs NORMAL ×3, RAMP ×3, FLASH_CROWD ×3, UDP_FLOOD ×3, DEGRADED ×3, each ~300 s.
* After the three NORMAL runs it **calibrates X automatically** and labels all runs.
* Do not load the VM meanwhile (builds, tests, training). A run is marked **invalid** if the VM CPU reaches 100 % during the
  measurement window.
* Interrupted or failed? Run the same command again: runs already `success` are skipped, the others are retried (attempts stay on record).
* Options: `--scenario S` (repeatable), `--repetition N`, `--duration SECONDS`, `--no-resume`, `--no-auto-calibrate`.

### 4.4 Check the outcome
```bash
.venv/bin/python -m netguard experiment report         # writes results/validation_report.md
cat results/validation_report.md
ls data/metadata | wc -l
```
Expected: 15 runs with status `success` (any `failed` attempt is retried by re-running 4.3). Then change ownership if needed:
`sudo chown -R $USER:$USER data results` (files written by sudo belong to root).

### 4.5 If you ever need to redo labels
```bash
.venv/bin/python -m netguard experiment calibrate --force    # only if you really want a new X
.venv/bin/python -m netguard experiment label                # recompute labels of all stored runs with the fixed X
```

Optional consolidated table of all successful runs: `.venv/bin/python -m netguard dataset build` → `data/processed/dataset.parquet`.

---------------------------------------------------------------------------------------------------------------------

## 5. Prepare the features (Phase 3, no root)

```bash
.venv/bin/python -m netguard prepare --expected-rows 300
cat results/prep_report.md
```
Validates each run (status, duplicates, gap-free 1 s timestamps, labels recomputed), excludes bad runs, builds the 105 features
(explained in plain English in [docs/ml-explanations/02-features.md](ml-explanations/02-features.md) and listed individually in
[docs/FEATURES.md](FEATURES.md)), splits **by run** (`three_way`: repetition 1 train / 2 validation / 3 test; or
`--scheme train2_test1`), fits the scaler on training rows only. Read the table "Splits" and "Labels by scenario": every split
should contain all five scenarios and some positive `future_congestion` rows.

---------------------------------------------------------------------------------------------------------------------

## 6. Train and evaluate (Phase 4/5, no root, ≈ 3 min)

```bash
.venv/bin/python -m netguard train --n-jobs 2
cat results/ml_results.md
```
Trains classification (DT/RF/XGB), anomaly detection (Isolation Forest/LOF + rules) and congestion prediction (LR/RF/XGB vs
baselines), writes `models/*.joblib`, `results/ml_results.md/json`. Restart the backend afterwards so it loads the new models.
*Why each model was picked, in plain English:* [docs/ml-explanations/01-models.md](ml-explanations/01-models.md).

---------------------------------------------------------------------------------------------------------------------

## 7. Plain live demo (real Mininet, streamed to the dashboard)

A real Mininet scenario, no attack website involved — good for showing the live pipeline on any of the 5 scenarios.

1. Start the **backend** (§3 terminal 1) and the **dashboard** (§3 terminal 2). Open http://127.0.0.1:5173, **Monitoring**
   tab, **Live monitoring** (green banner, "No live samples yet").
2. In a **third terminal** start a real live scenario **(sudo)**:

```bash
cd ~/Desktop/netguardAI/NetGuardAI
sudo .venv/bin/python -m netguard live ramp --duration 150
```

3. The dashboard fills in second by second (real measurements from the running network). Predictions appear after ~30 s
   (history needed for the lag features) and the view lags real time by ≈ 4 s. In the RAMP scenario congestion starts at ~60 s.
4. When the run ends the command prints the number of samples posted and a live-versus-offline comparison, then the session
   stays stored. Use the dropdown ("Past session: …") to play it back later (amber **RECORDED LIVE SESSION** banner).

Other scenarios: `udp_flood`, `flash_crowd`, `degraded`, `normal`. Options: `--duration`, `--seed` (default 7, different from
the dataset seeds), `--backend URL`, `--stream-id NAME`. The command refuses to start if the backend is not reachable.
Duration must be long enough for the scenario's own start times (use ≥ 150 s).

Your own monitor can also feed the dashboard: send one JSON object per second to `POST /api/ingest` (fields in
`netguard/backend/live.py: IngestSample`; unmeasured metrics must be `null`, never 0).

---------------------------------------------------------------------------------------------------------------------

## 8. Attack + mitigation demo (a real sample website, really attacked, really defended)

The headline demo: a real website inside the test network, a real UDP flood against it, and the project's own trained
models detecting it live — with an optional, automatic, real mitigation. Full walkthrough and talking points:
[docs/DEMO_GUIDE.md](DEMO_GUIDE.md).

### 8.1 What it does
* Hosts a small real website (**QuickCart Demo Store**) on Mininet host **h5**. For the flood's own window
  (**t = 120 s to t = 180 s**) it serves a clear, hardcoded **`503 Service Unavailable`** page instead of the normal one —
  a guaranteed-visible "the site is down", not left to chance.
* Probes that website every second from host **h2** with `curl`, logging status + response time.
* Runs the real `udp_flood` scenario: legitimate background traffic starts immediately; a real **20 Mbit/s UDP flood**
  from **h1 → h5** (double the 10 Mbit/s bottleneck) fires automatically at t ≈ 120 s and lasts 60 s.
* Streams every real second to the backend, scored live by the **same trained models** as §6 (XGBoost classifier,
  Isolation Forest anomaly detector, Logistic Regression congestion predictor).
* With **`--mitigate`**: the instant the model flags the flood, the script rate-limits the attacker (`h1`) for real with
  `tc`, and the website's error page is lifted immediately — not at t = 180 s.

### 8.2 Run it — two parts, same command

**Terminal 1** (if the backend isn't already running — see §3): `python -m netguard serve`

**Terminal 2:**
```bash
cd ~/Desktop/netguardAI/NetGuardAI

# Part 1 - show the site fail, undefended:
sudo .venv/bin/python scripts/demo_attack.py --duration 200
```
```bash
# Part 2 - show it detected AND mitigated (run again, after Part 1 finishes):
sudo .venv/bin/python scripts/demo_attack.py --duration 200 --mitigate
```

**Terminal 3** (either run — watch the website's real health):
```bash
tail -f /tmp/netguard_demo/site_probe.log
```

**Browser:** if the frontend (§3) is running, open http://127.0.0.1:5173 and click the **"Live attack demo"** tab — the
same detection dashboard, embedded, no extra file to find. Otherwise open
[`demo/dashboard.html`](../demo/dashboard.html) directly (double-click it) — it connects straight to the backend's
WebSocket, no build step needed either way.

### 8.3 What you'll see

| ~Time | Website probe (Terminal 3) | Dashboard |
|---|---|---|
| t = 0–30 s | `200 0.0Xs` every second | "Warming up…" |
| t = 30–120 s | `200 0.0Xs`, steady | Banner **Normal** |
| t ≈ 120 s | `503` starts appearing | Utilisation jumps toward 100%; classified `UDP_FLOOD` |
| t ≈ 121–180 s (Part 1) | `503` every second | Banner **⚠ ATTACK DETECTED — congestion imminent** |
| t ≈ 121 s+ (Part 2, `--mitigate`) | back to `200 0.0Xs` within seconds | Terminal prints `*** MITIGATION APPLIED ***`; banner stays detected but the site recovers |
| t ≈ 180 s+ | back to `200 0.0Xs` | Banner returns to **Normal** |

Key flags: `--mitigate-rate-mbps` (default 1.0), `--attacker-host`/`--server-host`/`--client-host` (default h1/h5/h2),
`--seed`, `--backend URL`, `--stream-id NAME`. Cleans up after itself (Ctrl-C safe); stray processes:
`sudo pkill -f "http.server 8081"; sudo pkill -f netguard_demo/probe.sh`.

---------------------------------------------------------------------------------------------------------------------

## 9. The 3D view tab (no backend data needed)

Open the dashboard (§3), click **3D view**. Drag anywhere on the dark stage to rotate it. Nine scenes in three groups —
good for explaining the CN/ML concepts visually, independent of any live run:

* **Network topology (2):** the experiment topology, and the live-attack-demo topology with roles highlighted.
* **ML model (2):** Isolation Forest's boundary illustration, Logistic Regression's decision-wall illustration.
* **Live simulations (5):** Queue & packet drop (token-bucket/bufferbloat), Attack & mitigation (with **Start/Stop
  attack** and **Mitigate** buttons — the same story as §8, animated, no Mininet needed), Traffic classification
  (shows the real ~90 % accuracy and the documented RAMP↔UDP_FLOOD confusion), Congestion building up (the exact real
  congestion rule), and TCP Handshake vs. SYN flood (ties to the `syn_ratio` feature).

Every scene's caption says plainly that it is illustrative, and points at the real numbers/code it represents.

---------------------------------------------------------------------------------------------------------------------

## 10. Giving a full demo (recommended order, ~20 minutes)

1. **Dashboard up** (§3): backend + frontend running, `http://127.0.0.1:5173` open.
2. **Monitoring → Historical replay:** RAMP test run, scrub to ~second 58 — the early warning, then congestion at 60;
   compare with the red ground-truth band. Switch to UDP_FLOOD and FLASH_CROWD to show the honest limitation (a
   legitimate crowd gets flagged too).
3. **Model performance tab:** read the real numbers and state the caveats out loud (few congestion onsets, one VM).
4. **3D view tab** (§9): walk through the two topologies and two models quickly; use this to explain CN/ML concepts
   without needing anything running underneath.
5. **Live attack demo, Part 1** (§8): run `scripts/demo_attack.py` (no `--mitigate`) in a terminal, follow along in the
   embedded **Live attack demo** tab and the `tail -f site_probe.log` terminal — the site visibly fails (`503`).
6. **Live attack demo, Part 2:** re-run with `--mitigate` — same attack, but now detected *and* fixed; the `503`s stop
   within seconds of the terminal printing `*** MITIGATION APPLIED ***`.
7. *(Optional)* **Plain live run** (§7): `netguard live ramp` for a non-attack scenario, to show the pipeline works on
   ordinary traffic too.
8. **Close** with the honest caveats (Part G/H of [docs/VIVA_DOCUMENTATION.md](VIVA_DOCUMENTATION.md)).

Presenting as a team of 3–4? [docs/PRESENTATION_SPLIT.md](PRESENTATION_SPLIT.md) splits this exact script across
speakers, and [docs/presentations/](presentations/) has ready-made `.pptx` decks (a 4-slide common deck plus one
individual-contribution deck per teammate).

---------------------------------------------------------------------------------------------------------------------

## 11. Tests

```bash
.venv/bin/python -m pytest -q                       # unit tests (~450, ~2 min, no root, no Mininet)
sudo mn -c                                          # clean leftovers first
sudo .venv/bin/python -m pytest -m integration tests/integration -v -s -rs      # real Mininet tests (sudo, ~2 min)
```
The unit tests use small synthetic tables purely to test code logic; no result in this project comes from them.
Integration tests build the real topology, check connectivity, `tc` shaping, TCP/UDP throughput, every traffic tool, and run a real
short experiment with all collectors, a failed experiment and the Mininet cleanup.

---------------------------------------------------------------------------------------------------------------------

## 12. Command reference

| Command | Root | What it does |
|---|---|---|
| `python -m netguard validate` | no | validate all YAML and plan every scenario |
| `python -m netguard list` | no | list scenarios |
| `python -m netguard plan <scenario> [--events] [--json] [--seed N] [--duration S]` | no | dry-run the traffic plan |
| `python -m netguard topology [--pingall] [--cli]` | yes | build the dumbbell and inspect it |
| `python -m netguard run <scenario> [--results-json F]` | yes | Phase 1: run one scenario once, no dataset |
| `python -m netguard experiment run [config] [--scenario S] [--repetition N] [--duration S] [--no-resume] [--dry-run]` | yes (not for `--dry-run`) | Phase 2 campaign |
| `python -m netguard experiment calibrate [--force]` | no | derive X from the NORMAL runs |
| `python -m netguard experiment label` | no | recompute labels of stored runs |
| `python -m netguard experiment report` | no | write `results/validation_report.md` |
| `python -m netguard dataset build` | no | merge successful runs into `data/processed/dataset.parquet` |
| `python -m netguard prepare [--scheme S] [--expected-rows N]` | no | Phase 3 features and splits |
| `python -m netguard train [--n-jobs N]` | no | Phase 4/5 models and evaluation |
| `python -m netguard serve [--port P]` | no | backend API |
| `python -m netguard live <scenario> [--duration S] [--stream-id NAME]` | yes | real live run streamed to the backend |
| `python scripts/demo_attack.py [scenario] [--duration S] [--mitigate] [--mitigate-rate-mbps R]` | yes | attack + mitigation demo (§8) |
| `npm run dev` (inside `frontend/`) | no | the dashboard (Monitoring, Model performance, 3D view, Live attack demo) |

Backend environment variables: `NETGUARD_DATA_DIR`, `NETGUARD_MODELS_DIR`, `NETGUARD_RESULTS_DIR`, `NETGUARD_DB`.
Add `--log-level DEBUG --log-file FILE` before the sub-command for detailed logs.

---------------------------------------------------------------------------------------------------------------------

## 13. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `needs root` / `Mininet must run as root` | run the command with `sudo .venv/bin/python …` (the venv's Python, not system Python) |
| `[Errno 13] Permission denied` around TShark | Ubuntu's AppArmor profile for tshark; already handled (root-owned temp folder, `-a duration`). If it returns, check `journalctl -k | grep apparmor` |
| Leftover interfaces / "File exists" | `sudo mn -c`, then retry. The orchestrator also cleans before and after each run |
| Run marked `invalid` | VM CPU reached 100 % during the run: give the VM more cores, keep it idle, re-run the command (it retries) |
| Run marked `failed` | read `errors` in `data/metadata/<id>.json`; common cause: a traffic client overran — re-run |
| `address already in use` (port 8000/5173) | a server is already running: use it (`curl 127.0.0.1:8000/api/health`), or `pkill -f "netguard serve"` / `pkill -f vite` |
| Dashboard says it cannot reach the backend | start the backend (§3); the dashboard proxies `/api` and `/ws` to port 8000 |
| `prepare`: "threshold X is not calibrated" | run the NORMAL runs first or `experiment calibrate` |
| `prepare`: "no valid runs" | every run was excluded; open `results/prep_report.md` for the reasons |
| Live tab shows nothing | no `live` run or attack demo is feeding the backend; start §7 step 2 or §8 |
| Predictions "warming up" | normal: the first 30 s of any run or stream have no lag features |
| Files in `data/` owned by root | `sudo chown -R $USER:$USER data results` |
| Models changed but dashboard shows old numbers | restart the backend |
| The demo website shows a `503` page | **expected**, not a bug — it's the hardcoded error page for the flood's window (§8.1); it lifts itself at t=180s, or immediately with `--mitigate` |
| `scripts/demo_attack.py`: "backend not reachable" | start it first — `python -m netguard serve` (§3 terminal 1) |

---------------------------------------------------------------------------------------------------------------------

## 14. Safety notes
* All traffic, including the UDP flood, stays between Mininet hosts in a private `10.0.0.0/24`; IP addresses in scenarios are rejected and every host's real address is checked before traffic starts.
* hping3 is always bounded (`-c`) and rate-limited, never `--flood`, spoofed or random-source.
* The system recommends only. It never changes the network configuration based on model output — the attack demo's
  `--mitigate` throttling is a deliberate, demo-only addition on top of that, clearly called out as such in
  [docs/DEMO_GUIDE.md](DEMO_GUIDE.md).
* `mn -c` / the orchestrator's cleanup removes **all** Open vSwitch bridges: use a VM dedicated to this project.

---------------------------------------------------------------------------------------------------------------------

## 15. Further documentation

| Doc | What's in it |
|---|---|
| [README.md](../README.md) | architecture and full reference |
| [docs/DEMO_GUIDE.md](DEMO_GUIDE.md) | the attack + mitigation demo, in full detail |
| [docs/ml-explanations/](ml-explanations/) | the 3 ML models and the 105 features, explained in plain English |
| [docs/cn_concepts/](cn_concepts/) | every Computer Networks concept used, with "say this to your professor" points |
| [docs/FEATURES.md](FEATURES.md) | all 105 features individually explained |
| [docs/VIVA_DOCUMENTATION.md](VIVA_DOCUMENTATION.md) | full viva/presentation documentation and Q&A prep |
| [docs/PRESENTATION_SPLIT.md](PRESENTATION_SPLIT.md) | the live demo script split across 4 speakers |
| [docs/presentations/](presentations/) | ready-made `.pptx` decks: one common (4 slides) + one per teammate |
