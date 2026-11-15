# NetGuard AI — Complete Project Documentation (for viva and presentation)

This document explains the whole project **from zero**: the problem, every tool, every specification, how each feature is
built, what the results mean and what questions to expect. No prior knowledge of networking or machine learning is assumed.
Everything numeric below comes from the real experiments run in this project.

**How to read it:** Part A (plain-language idea and vocabulary) → Part B (tools) → Part C (specifications) → Part D (how every
feature is implemented) → Part E (a worked example) → Part F (results) → Part G (problems solved) → Part H (viva questions
and answers) → Part I (presentation plan) → Part J (cheat sheet).

---------------------------------------------------------------------------------------------------------------------

# PART A — The idea in plain language

## A1. The problem
A computer network is like a road system. Data travels in small pieces called **packets**. If too many cars enter a road
that is too narrow, a **traffic jam** forms: delays grow and some cars are lost. In a network this jam is called
**congestion**. Network administrators want to:

1. notice **unusual traffic** (an attack, a failure, or just a crowd),
2. tell the difference between a **harmless crowd** (thousands of real users arriving at once) and a **malicious flood**,
3. see a jam **coming before it happens**, so they can react in time.

## A2. What NetGuard AI does
NetGuard AI is a software system that **watches a network once per second**, uses **machine learning (ML)** to judge what it
sees, and shows the result on a **dashboard**:

* **Anomaly detection:** "is this traffic unusual, and what kind of unusual?"
* **Traffic classification:** "which kind of situation does this look like (normal, ramp-up, crowd, flood, degraded link)?"
* **Congestion prediction:** "will the network be congested within the next **30 seconds**?"
* **Recommendations:** advice only (the system never changes the network by itself).

## A3. Why we built our own network and data
Real networks cannot be attacked or overloaded on purpose, and public datasets do not contain the measurements needed to
predict congestion (queue size, link capacity). So we built a **virtual network inside one computer** using Mininet, generated
different kinds of traffic on it, **measured everything once per second**, labelled each second with the true situation, and
trained ML models on that data. All traffic, including the "attack", stays inside the virtual network.

## A4. The five situations (scenarios) we studied

| Scenario | In everyday words | Why it matters |
|---|---|---|
| NORMAL | an ordinary office day: browsing, file transfers, DNS lookups | the baseline of "healthy" |
| RAMP | traffic grows step by step until the road is full | to learn the signs that precede a jam |
| FLASH_CROWD | many real users arrive suddenly (a famous website after a news item) | harmless but looks like an attack; tests false alarms |
| UDP_FLOOD | a few machines blast data at maximum rate | looks like a denial-of-service attack |
| DEGRADED | the road itself is bad (extra delay and random loss) while traffic is normal | slow network *without* overload |

## A5. Vocabulary (glossary)

| Term | Meaning (plain words) |
|---|---|
| **Packet** | one small piece of data on the network (like one envelope) |
| **Bandwidth / capacity** | how much the link can carry per second (our bottleneck: **10 Mbit/s**) |
| **Throughput** | how much is *actually* carried per second |
| **Utilisation** | throughput ÷ capacity, in % (100 % = road full) |
| **Latency / RTT** | **round-trip time**: how long a message takes to go there and back (here ≈ 40 ms when idle) |
| **Jitter** | how much the RTT wobbles from one moment to the next |
| **Packet loss** | the percentage of packets that never arrive |
| **Queue / buffer** | the waiting line in front of a link; when it is full, new packets are dropped |
| **Queue delay** | how long a packet waits in that line (queue size ÷ link speed) |
| **Bottleneck** | the narrowest link in the path, where jams happen |
| **Congestion** | the bottleneck is nearly full **and** the queue/loss is high |
| **TCP** | reliable protocol (re-sends lost data; used by web, file transfer) |
| **UDP** | fast, no-guarantee protocol (video, DNS, floods) |
| **ICMP** | the protocol behind `ping` |
| **DNS / HTTP** | name lookup / web page requests |
| **SYN** | the first packet that opens a TCP connection; a flood of SYNs is a classic attack sign |
| **Retransmission** | TCP sending a lost packet again (a sign of trouble) |
| **Flow** | one conversation: same source, destination, ports and protocol |
| **Connection** | an open TCP conversation |
| **Anomaly** | something that does not look like the normal data |
| **Feature** | one number given to the ML model (e.g. "average throughput of the last 5 seconds") |
| **Label** | the correct answer for a row of data (e.g. "congested = yes") |
| **Training / validation / test data** | data to learn from / to tune on / to judge honestly on at the end |
| **Data leakage** | accidentally letting the model peek at the answer or the future; makes results fake |
| **Precision / recall / F1** | of the alarms raised, how many were right / of the real events, how many were caught / their balance |
| **False-positive rate (FPR)** | how often a harmless second is wrongly flagged |
| **PR-AUC** | a single score (0–1) of how well alarms are ranked; higher is better |
| **API / REST** | a way for programs to ask the backend for data over HTTP |
| **WebSocket** | a connection that lets the server **push** new data continuously |

---------------------------------------------------------------------------------------------------------------------

# PART B — The tools used, what each one is and why

## B1. Platform
| Tool | What it is | How it is used here |
|---|---|---|
| **Ubuntu Linux (in a VMware VM)** | operating system with the networking tools needed | everything runs here; the VM is dedicated so cleanup tools may remove all virtual bridges |
| **Python 3.14** | programming language | all backend, data and ML code |
| **Git / GitHub** | version control | source code is backed up in the repository |

## B2. Network simulation and traffic generation
| Tool | What it is | How it is used |
|---|---|---|
| **Mininet** | creates a whole virtual network (hosts, switches, links) inside one machine using Linux namespaces | builds the dumbbell topology; each virtual host can run real programs |
| **Open vSwitch (OVS)** | a software network switch | the two switches `s1`, `s2`; "standalone" mode = ordinary learning switch, no controller |
| **Linux Traffic Control `tc`** | kernel feature that shapes traffic | **netem** adds delay and random loss; **tbf** (token bucket filter) limits the rate and holds a small queue (50 packets) — this creates the bottleneck |
| **iperf3** | traffic generator and throughput tester | TCP and UDP streams at set rates; JSON results |
| **hping3** | packet crafting tool | optional bounded UDP streams (never `--flood`, never spoofed) |
| **ping** | sends ICMP echo | background traffic **and** our RTT/loss probe (every 0.2 s) |
| **Scapy** | Python packet library | builds and parses DNS queries for the DNS traffic generator |
| **Python HTTP/DNS workers** | small servers/clients we wrote | create web-like and DNS traffic (a new TCP connection per request) |
| **ethtool** | NIC settings | turns off offloads (TSO/GSO/GRO) so `tc` sees real packets |

## B3. Measurement and monitoring (collectors)
| Tool | What it measures | Notes |
|---|---|---|
| **`ip -s link`** (iproute2) | bytes and packets per interface → throughput, packets/s, utilisation | read twice per second boundary; rate = difference ÷ time |
| **`tc -s qdisc`** | queue backlog, drops, packets → queue delay, loss % | JSON output; tbf backlog ÷ link rate = queue delay |
| **`ss -ti`** | open TCP connections and retransmission counters | run inside each host's namespace via `nsenter` |
| **ping probe** | RTT, jitter, probe loss | 5 probes per second |
| **psutil** | VM CPU and RAM | a run is **invalid** if CPU reaches 100 % |
| **TShark** (command-line Wireshark) | captures packets and counts protocols | header-only capture (first 96 bytes) so files stay small |
| **NFStream** | turns a packet capture into **flow records** | counts new/active flows, SYN packets, per-flow statistics |
| **Wireshark** | graphical packet viewer | only for manual inspection |

## B4. Data processing and machine learning
| Tool | Role |
|---|---|
| **pandas, NumPy** | tables and numbers (per-second data, rolling windows) |
| **PyArrow / Parquet** | compact, fast, typed file format for the datasets |
| **scikit-learn** | Decision Tree, Random Forest, Logistic Regression, Isolation Forest, Local Outlier Factor, scaling, metrics |
| **XGBoost** | gradient-boosted trees (strong, fast model) |
| **SciPy** | scientific library (a dependency of scikit-learn) |
| **joblib** | saves/loads trained models |
| **pytest** | automated tests (≈ 450 unit tests + integration tests) |

## B5. Backend, storage and dashboard
| Tool | Role |
|---|---|
| **FastAPI** | the web API framework (fast, automatic documentation at `/docs`) |
| **Uvicorn** | server that runs FastAPI |
| **Pydantic** | validates incoming JSON (e.g. live samples) |
| **SQLAlchemy + SQLite** | stores alerts and live records in one lightweight file |
| **WebSockets** | pushes live and replayed data to the browser second by second |
| **React + TypeScript** | the dashboard (components, type safety) |
| **Vite** | development server and bundler for the dashboard |
| **Tailwind CSS** | styling |
| **Recharts** | charts |
| **Node.js / npm** | runs the frontend tooling |

*(Matplotlib is installed as a planned plotting tool but the system does not use it.)*

---------------------------------------------------------------------------------------------------------------------

# PART C — Specifications

## C1. Network
```
h1 ─┐                                        ┌─ h5
h2 ─┼─ s1 ═══ bottleneck 10 Mbit/s, 20 ms ═══ s2 ─┼─ h6      subnet 10.0.0.0/24, host hK = 10.0.0.K
h3 ─┤                                        ├─ h7
h4 ─┘     access links 100 Mbit/s              └─ h8
```
* Bottleneck: **10 Mbit/s**, **20 ms one-way delay in each direction (≈ 40 ms RTT)**, queue of **50 packets** (≈ 60 ms), 0 % random loss
  (DEGRADED adds **+60 ms** delay and **3 %** loss).
* Access links 100 Mbit/s (always faster than the bottleneck; the code refuses otherwise).
* All shaping applied to both ends of each link and **read back from the kernel and verified**.

## C2. Experiments
* 5 scenarios × 3 repetitions × **300 s** = **15 runs**, seeds **42, 43, 44** (recorded in each run's metadata).
* Each run: 5 s pre-roll + 300 s + 3 s tail of collection; only the 300 s become dataset rows.
* Measurement every **1 s**; ping probes every **0.2 s**; TShark capture snaplen **96 bytes**.
* A run's status: **success**, **failed** (a collector or traffic event failed, cleanup not clean) or **invalid** (VM CPU reached 100 %).
  Result: 15 successful runs, 2 failed attempts recorded and excluded.

## C3. Dataset
* **4,500 rows** (15 × 300 s), one row per second, about 66 columns before feature engineering.
* **105 model input features** after engineering.
* Stored as **Parquet**; metadata as JSON; every run keeps its ID, scenario, repetition, seed, network configuration, timestamps, status and errors.

## C4. Labels (ground truth)
| Label | Meaning |
|---|---|
| `scenario_label` | NORMAL / RAMP / FLASH_CROWD / UDP_FLOOD / DEGRADED |
| `current_congestion` | this second is part of a congested episode |
| `future_congestion` | a **new** congestion episode starts within the next 30 s |
| `time_to_congestion` | seconds to the next onset (**only for evaluation, never a model input**) |

**Congestion rule:** utilisation ≥ **90 %** AND (queue delay ≥ **X = 29 ms** OR loss > **1 %**), lasting ≥ **3 consecutive seconds**.
X was **calibrated** from the NORMAL runs (method in D5) and frozen.

## C5. Models (fixed settings, no heavy tuning)
| Task | Model | Main settings |
|---|---|---|
| Classification | Decision Tree | max depth 8, balanced classes |
| | Random Forest | 150 trees, depth 12, balanced |
| | XGBoost | 150 trees, depth 4, learning rate 0.1 |
| Anomaly | **Isolation Forest** (primary) | 200 trees, trained on NORMAL training rows only |
| | Local Outlier Factor | 20 neighbours, novelty mode, NORMAL only |
| | Rule layer | explains the cause of each flagged second |
| Congestion | Logistic Regression | balanced class weights |
| | Random Forest | 150 trees, depth 10, balanced |
| | XGBoost | 150 trees, depth 3, class weighting |
| Baselines | persistence, utilisation > 80 %, rising utilisation | no learning |

## C6. Splits (by run, never by row)
repetition 1 → **train**, repetition 2 → **validation**, repetition 3 → **test**. Every scenario appears in every split.
Scaling is learned on the training rows only.

## C7. Software interfaces
Backend on port **8000** (REST + WebSockets), dashboard on port **5173**, database `data/backend/netguard.db`.

---------------------------------------------------------------------------------------------------------------------

# PART D — How every feature is implemented

## D1. Building the virtual network (`netguard/topology/`)
1. `dumbbell.py` describes the topology (2 switches, 4 hosts per side, one inter-switch link) and creates it with Mininet.
2. `tc.py` builds the shaping commands: a **netem** qdisc (delay, loss) with a **tbf** child (rate, burst, queue limit). Netem
   sits in front because netem + tbf is the recommended way to combine delay with a rate limit.
3. After starting, every interface's qdisc is **read back** and compared with the configuration; any mismatch aborts the run.
4. All sizes (hosts, rates, delays) come from `configs/topology.yaml` and `configs/links.yaml` — nothing is hard-coded in commands.

## D2. Generating traffic (`netguard/scenarios/`, `netguard/traffic/`)
* A scenario YAML lists **components** (e.g. "2 paced TCP clients at 0.8 Mbit/s"). Each has a **pattern** that decides timing:
  NORMAL (steady), BURST, PERIODIC, RANDOM, **RAMP** (staircase to above capacity at 60 s), **FLASH_CROWD** (many clients within seconds),
  **UDP_FLOOD** (rate-limited high-rate UDP).
* `patterns.py` + `planner.py` turn this into a list of timed **events**, using a **seeded random generator** → same scenario and seed always give the same events (reproducible).
* `executor.py` runs the events on the virtual hosts at the right times (one thread per event), starts servers before clients,
  kills anything that overruns, and records each event as ok/failed/skipped.
* Safety: hosts are accepted only as names (never raw IPs), each host's real IP is verified to be inside `10.0.0.0/24`, and
  limits on rates and counts live in `traffic.yaml`.

## D3. The experiment orchestrator (`netguard/experiments/orchestrator.py`)
One command runs a whole campaign. For each run it does, in order:
1. **Clean** Mininet/OVS leftovers (`lifecycle.py`), build and shape the network, optional all-pairs ping check.
2. Start the TShark capture, then compute the **shared clock** (`timeline.py`): scenario second 0 starts 5 s later.
3. Start all collectors; each samples at the **same wall-clock instant** every second, so rows line up.
4. Run the traffic plan (the executor is told the exact start instant).
5. Stop collectors, tear down Mininet, **verify** the cleanup.
6. Derive per-second metrics, extract flows with NFStream, compute labels, write Parquet and metadata.
7. Decide the status. Failures are **recorded with the error**; missing values stay empty (never invented).
8. **Resume:** re-running skips runs that already succeeded and retries the others.

## D4. The collectors and their formulas (`experiments/collectors/`)
All use second *k* = the interval between ticks *k* and *k+1*.

| Metric | Formula / method |
|---|---|
| Throughput (Mbit/s) | (tx_bytes[k+1] − tx_bytes[k]) × 8 ÷ elapsed time ÷ 10⁶, on the switch port that sends toward the other side |
| Packets/s | difference of tx_packets ÷ elapsed time |
| Utilisation (%) | throughput ÷ 10 Mbit/s × 100 (bytes include the Ethernet header, so a full link reads ≈ 100.9 %) |
| Queue delay (ms) | tbf backlog bytes × 8 ÷ link rate × 1000 |
| Drops / loss % | drops ÷ (delivered + drops) in that second; the worse of the two directions is used |
| RTT, jitter | per-second mean RTT; jitter = mean absolute difference of consecutive RTTs |
| Probe loss | lost probes ÷ sent probes (send time inferred from the reply time minus RTT) |
| Active connections | number of distinct ESTABLISHED TCP connections (both endpoints de-duplicated) |
| TCP retransmissions | new retransmitted segments since the previous tick, from `retrans:<now>/<total>` per socket |
| Protocol counts | TShark counts TCP/UDP/ICMP packets per second; DNS = UDP port 53; HTTP = the web server's TCP port |
| New / active flows | NFStream flows: **new** = flows whose first packet is in that second; **active** = flows between their first and last packet |

If a measurement is missing or the timing is off (elapsed time not between 0.5 and 1.5 s, counter reset) the value stays
**empty**; it is never filled in.

## D5. Labels and the calibration of X (`labels.py`, `calibration.py`)
**Algorithm (per run):**
1. For each second: `cond = utilisation ≥ 90 % AND (queue delay ≥ X OR loss > 1 %)` (three-valued: true / false / unknown).
2. A **congested episode** = a run of at least 3 consecutive true seconds; its first second is the **onset**.
3. `current_congestion[t]` = t is inside an episode.
4. `future_congestion[t]` = an onset exists in (t, t+30]. (A second inside an episode looks for the *next separate* episode, so the model cannot score by merely noticing the present.)
5. `time_to_congestion[t]` = (next onset) − t.
6. The last 30 s of each run cannot have a valid future label and are marked unavailable.
7. If a needed measurement is unknown the label is **null**, never guessed (labels are computed both assuming unknowns false and true; they must agree).

**Calibrating X (no arbitrary choice):** pool all NORMAL-run seconds with utilisation < 90 %, take the **99.9th percentile of
queue delay**, round up to a whole millisecond (minimum 1 ms) → **X = 29 ms**. It is stored in `data/metadata/calibration.json`
and never changed afterwards.

## D6. Feature engineering (`netguard/prep/`)
Each run is processed **alone** (nothing crosses a run boundary). Per second the system builds:

| Group | Count | Detail |
|---|---|---|
| Base signals | 15 | throughput, packets/s, utilisation, RTT, jitter, probe loss, loss %, queue backlog, queue delay, queue drops, active connections, TCP retransmissions, active flows, new flows, SYN ratio |
| Protocol shares | 5 | TCP, UDP, ICMP, DNS, HTTP as a fraction of all packets in the second |
| 5-second windows | 60 | for each base signal: **mean, max, standard deviation, slope** of the last 5 seconds (slope = least-squares trend per second) |
| Lag features | 24 | the value 5, 10 and 30 s earlier for 8 key signals |
| Causal congestion state | 1 | "has the congestion condition held for the last 3 s?" using only the past |
| **Total** | **105** | |

**SYN ratio** = SYN packets of flows that started in that second ÷ TCP packets in that second.

**Leakage prevention (key viva point):**
* windows are *trailing* (past + current second only); lags are positive shifts;
* the first 30 s of each run are left empty (not enough history) and **excluded from training**, never back-filled;
* labels, IDs and `time_to_congestion` are **never inputs**; even the ground-truth `current_congestion` is excluded because it needs the next 2 s;
* VM CPU/RAM are excluded (they describe the test computer, not the network);
* runs are split **as whole runs**; the scaler is fitted on training rows only;
* an automated test proves a feature at second *t* does not change if the future is altered.

**Validation before use** (`validate.py`): status, duplicates, gap-free 1 s timestamps, required columns, and a **recomputation of the
labels** that must equal the stored ones; failing runs are excluded with the reason in `results/prep_report.md`.

## D7. The machine-learning models (`netguard/ml/`)
### Traffic classification
Decision Tree, Random Forest and XGBoost learn to name the scenario of each second from the 105 features.

### Anomaly detection — two stages
* **Stage 1 (unusual?)** An **Isolation Forest** learns what NORMAL looks like by repeatedly splitting the data at random; unusual points
  are isolated in fewer splits and get a higher *anomaly score*. A second is flagged if its score exceeds a **threshold set on the NORMAL validation runs
  (95th percentile ⇒ about 5 % false alarms allowed)**. **LOF** (compares a point's local density with its neighbours') is the comparison model.
* **Stage 2 (what kind?)** A transparent rule layer reads a flagged second and names the cause with the numbers that triggered it:
  *congestion* (congestion state true), *flood-like* (≥ 80 % UDP, high utilisation, few new flows), *legitimate burst* (many new flows or connections, mostly TCP, no sustained loss),
  *degradation* (high RTT/jitter/loss while utilisation is below 50 %), else *unclassified*. The thresholds are NORMAL-traffic quantiles, and the rules never see the scenario label.
* An anomaly score shows **unusual**, not **malicious**; that is why stage 2 exists.

### Congestion prediction
Logistic Regression (a weighted sum turned into a probability), Random Forest and XGBoost output **P(new congestion within 30 s)**.
The warning threshold is chosen on the **validation** runs (best F1) and then applied unchanged to the test runs. They are compared with three
no-learning baselines and evaluated:
* on all rows, **and** on seconds that are **not yet congested** (the honest test, since "already congested" is easy);
* by **onset detection** (was each congestion onset warned about?) and **lead time** (seconds between first warning and onset).

### Resource measurements
Per-prediction latency (ms), CPU time, memory and saved-model size are measured for each model.

## D8. Backend (`netguard/backend/`)
* `inference.py` loads the saved models and, for a table of features, returns for each second: scenario label and probabilities, anomaly score/flag/cause/severity/reasons, and congestion probability/warning/window.
  If the 30 s of history are missing, the answer is `available: false` with a reason (nothing guessed).
* `replay.py` serves the 15 recorded runs: per-second metrics, **predictions and ground truth kept separate**, flagged `source: replay, historical: true`, and whether the run was used in training.
* `live.py` + `POST /api/ingest`: accepts one live sample per second, keeps the last 45 s per stream, builds the **same features** and predicts; metrics sent as `null` stay missing and withhold dependent predictions; old timestamps are rejected and a time gap restarts the stream.
* `db.py`: SQLite tables for **alerts** and **live monitoring records**; `GET /api/live/streams` lists past live sessions.
* WebSockets: `/ws/replay/{id}` streams a recording; `/ws/live` pushes each live record.

## D9. Real live monitoring (`netguard live`)
1. The command checks the backend is reachable, then runs a **real** Mininet scenario using the same orchestrator.
2. A **live packet reader** (`livepackets.py`) runs `tshark -l` and counts, per second, protocol packets, new flows, active flows and SYN packets.
3. A **feeder** (`livefeed.py`) waits ≈ 4 s after each second ends (the flow definition needs each flow's last packet), computes that second's metrics with the **same functions as the dataset**, and POSTs them to the backend.
4. The dashboard (WebSocket) shows them immediately; the backend stores them so the session can be replayed later.
5. **Verification:** after the run the saved offline data of the same run is compared with what was sent live. Result: all metrics identical except the active-flow count (difference ≈ 0.007 flows per second on average), and the predictions made live **agreed with the offline predictions on 100 % of seconds**.

## D10. Dashboard (`frontend/`)
* **Banner:** amber = historical, green = live; a past live session has its own amber banner. Nothing historical is ever shown as live.
* **Run selector and player:** play/pause/speed/scrub; warns if the run was used for training.
* **Panels:** overview cards (throughput, utilisation, loss, RTT, jitter, CPU, memory, network condition); throughput/utilisation; latency, queue delay and loss; flows and connections; protocol mix; **congestion prediction** (probability, threshold, predicted window t+1…t+30 s, ground truth shading); **anomaly detection** (score, cause, reasons, suggested action); **alerts**; **model performance** tables.
* The dashboard only *recommends*; it never changes the network.

## D11. Quality assurance
* ≈ **450 unit tests** (labels, features and leakage, collectors' maths, orchestrator failure handling, models, API, live code) + **15 integration tests** on real Mininet.
* Synthetic tables appear **only inside tests** to check code logic; no result in the project comes from them.

---------------------------------------------------------------------------------------------------------------------

# PART E — A worked example (RAMP, held-out test run)

1. The scenario raises UDP load in steps: 10 % of capacity at the start, **105 %** at second 60.
2. By second ~50 utilisation reaches 90 %; the queue builds. At second **60** the congestion rule has held 3 seconds → **onset at 60**.
3. The dashboard at **second 58** shows: utilisation 101 %, congestion probability **95 %** (threshold 65 %), **WARNING** — two seconds *before* the onset.
   The congestion model actually crossed its threshold much earlier, from the first second a prediction was possible (second 30).
4. The anomaly stage flags the traffic and the rule layer says "flood-like" because the load is almost entirely UDP. **That explanation is imperfect:** RAMP is a harmless-by-design load test, but its signature (UDP, high rate) resembles a flood.
5. After second 60, loss reaches ≈ 25 % and queue delay ≈ 60 ms: the network is congested (`current_congestion = true`).
6. In this test recording the congestion has short gaps, so later onsets are found at seconds 181 and 211 (in repetition 1 they are at 126 and 201). By the rule, each new episode counts as a new onset.

---------------------------------------------------------------------------------------------------------------------

# PART F — Results and how to interpret them (held-out test runs)

| Task | Result |
|---|---|
| Classification | Random Forest **0.908** accuracy, XGBoost 0.901, Decision Tree 0.893 (macro F1 0.906 / 0.896 / 0.887). UDP_FLOOD is the hardest class (recall 0.60). |
| Anomaly detection (any non-NORMAL second = positive) | Isolation Forest precision **0.97**, recall **1.00**, F1 0.985, but **12.2 % false positives on NORMAL** and it flags **100 % of FLASH_CROWD** (legitimate). LOF: precision 0.96, recall 0.97, FPR 15.9 %. |
| Rule layer | agrees with the true scenario only ≈ 50 % (FLASH_CROWD and UDP_FLOOD often "unclassified"; RAMP called "flood-like"). |
| Congestion prediction (not-yet-congested seconds) | Logistic Regression precision **0.87**, recall 0.52, F1 **0.65**, warned before **5 of 6** onsets (mean lead ≈ 30 s). XGBoost F1 0.53 (precision 1.00), Random Forest 0.42. Baselines: utilisation > 80 % F1 0.33; persistence cannot warn early (recall 0). |
| Speed | Logistic Regression ≈ 0.2 ms, XGBoost ≈ 0.3–0.4 ms, Random Forest ≈ 90–100 ms per prediction; model files from a few KB to ≈ 2.6 MB. |

**Honest interpretation**
* The anomaly detector is very sensitive: it catches every non-normal second but also raises alarms on normal-but-different ones. It is a first-stage filter, not a verdict.
* The congestion model **beats the simple baselines** in this experiment, but the test set contains only **6 onsets** (one run per scenario) → the result is **promising, not conclusive**.
* The first 30 s of any run have no prediction, so a "30-second lead" is partly the warm-up.
* Everything comes from one virtual network (one topology, one bottleneck).

---------------------------------------------------------------------------------------------------------------------

# PART G — Problems met and how they were solved (good viva material)

| Problem | Cause | Solution |
|---|---|---|
| `[Errno 13] Permission denied` in real experiments | Ubuntu's AppArmor profile confines tshark: it could not write into other users' folders and rejected signals from other processes | capture into a root-owned temporary folder and end the capture itself with `-a duration`; detected by reading the kernel log |
| A run was labelled failed after a TShark start-up error | TShark printed "Capturing on…" before failing to open its file | after start-up the process is checked again and its error is reported |
| Campaign aborted after the NORMAL runs | automatic calibration raised an error with too little data | it now logs a warning; labels stay pending instead of crashing |
| Live congestion warnings disagreed with offline ones (63 % agreement) | live "active flows" counted flows only in seconds with a packet, offline counts every second of a flow's life | adopt the offline definition with a 4 s look-ahead → 100 % agreement |
| Possible fake accuracy | the true `current_congestion` needs the next 2 s | excluded as an input; a past-only `congested_causal` feature added |
| Too little history at run start | lag features need 30 s | left empty and excluded, never filled |
| A failed run could look successful | silent failures | every run has a status; failed and invalid runs are stored but excluded |

---------------------------------------------------------------------------------------------------------------------

# PART H — Viva questions and model answers

**Q1. What is NetGuard AI in one sentence?**
A system that measures a network every second, uses ML to flag unusual traffic, explain why, and warn 30 seconds before congestion, with a live dashboard.

**Q2. Why Mininet and not a real network?**
It lets us create congestion and attack-like traffic safely and repeatably inside one computer using real Linux networking, with known ground truth.

**Q3. Why a dumbbell topology?**
It has one clear bottleneck, so congestion has one obvious location and cause. It is the standard topology for congestion studies.

**Q4. How is the bottleneck created?**
With Linux `tc`: a token-bucket filter limits the rate to 10 Mbit/s with a 50-packet queue, and netem adds 20 ms delay (and loss in DEGRADED).

**Q5. What is congestion in your project, exactly?**
Utilisation ≥ 90 % and (queue delay ≥ 29 ms or loss > 1 %) for at least 3 consecutive seconds.

**Q6. Why 29 ms? Isn't that arbitrary?**
No: it is the 99.9th percentile of queue delay on healthy NORMAL seconds (utilisation < 90 %), rounded up, computed before training and then frozen.

**Q7. What are the labels and which one is dangerous?**
`current_congestion`, `future_congestion`, `time_to_congestion`, `scenario_label`. `time_to_congestion` is dangerous because it contains the answer; it is never an input. Even `current_congestion` is excluded because it depends on the next two seconds.

**Q8. How do you prevent data leakage?**
Only past/current windows and lags; labels never as inputs; split by whole runs; scaler fitted on training only; incomplete early seconds excluded; a test proves features ignore the future.

**Q9. Why split by run and not randomly by row?**
Neighbouring seconds are almost identical; a random split would put near-copies in train and test and give fake accuracy. Each run is independent.

**Q10. How much data do you have?**
15 runs × 300 s = 4,500 rows, 105 features; about 4,050 rows have a valid 30 s label.

**Q11. Why Isolation Forest?**
It needs no attack examples: it learns normal behaviour and isolates outliers, is fast on a CPU and small. LOF is the comparison.

**Q12. Does a high anomaly score mean an attack?**
No. It only means unusual. A flash crowd is unusual but legitimate. That is why a second stage explains the likely cause.

**Q13. Your detector flags FLASH_CROWD. Is that a failure?**
It is a limitation we report openly: stage 1 flags 100 % of that legitimate burst; stage 2 identifies it as a "legitimate burst" only part of the time. Improving that is the main future work.

**Q14. What is precision vs recall?**
Precision: of the alarms, how many were real. Recall: of the real events, how many were caught. Our anomaly recall is 1.0 but precision comes with a 12 % false-alarm rate on normal traffic.

**Q15. What is PR-AUC?**
A score (0–1) summarising the precision-recall trade-off across all thresholds; better than accuracy when events are rare.

**Q16. Can your system really predict congestion 30 seconds ahead?**
Within this experiment the best model warned before 5 of 6 onsets with F1 0.65 on not-yet-congested seconds, clearly better than simple baselines. But with only 6 test onsets and one virtual network, it is evidence, not proof.

**Q17. Why evaluate on "not yet congested" seconds?**
Predicting congestion when the network is already congested is trivial. The meaningful question is whether the model warns *before*.

**Q18. What is the lead time?**
Seconds between the first warning and the real onset. Mean ≈ 30 s for Logistic Regression (limited by the 30 s horizon and the 30 s warm-up).

**Q19. Why is Logistic Regression best? Isn't a simpler model worse?**
With so few congestion examples, a simple model overfits less than forests or boosted trees; it is also the fastest (0.2 ms).

**Q20. What are the baselines and why?**
Persistence (predict congestion if it already exists), utilisation > 80 %, rising utilisation. They show whether ML adds value over simple rules.

**Q21. How are measurements collected?**
Every second: interface counters, `tc` queue statistics, `ss` connection data, ping probes, psutil, TShark and NFStream; all sampled on one shared clock.

**Q22. What is NFStream and why both TShark and NFStream?**
TShark captures packets and counts protocols; NFStream groups packets into flows and computes flow statistics. They do different jobs.

**Q23. What if a measurement fails?**
It stays empty; labels and predictions that depend on it are withheld; a run with failed collectors is marked failed. Nothing is invented.

**Q24. What makes a run invalid?**
VM CPU reaching 100 % during the measurement window, because the computer itself would distort the measurements.

**Q25. How do you know live mode is correct?**
After a real live run, the same run was recomputed offline: metrics matched, and live predictions agreed with offline predictions on 100 % of seconds.

**Q26. Why does live mode lag by ~4 seconds?**
To know whether a flow has ended, you need to see its last packet; waiting 4 s reproduces the offline definition.

**Q27. How does the dashboard avoid showing fake data?**
Every record has `source` and `historical`; replay is amber, live is green; with no live monitor connected it shows nothing.

**Q28. Is the traffic generator dangerous?**
No: traffic exists only between virtual hosts in a private subnet, addresses outside it are rejected, rates are capped, hping3 is bounded and rate-limited.

**Q29. Does the system change the network?**
Never. It recommends only.

**Q30. What would you improve?**
More runs and more topologies; test on real traffic; better anomaly rules for flash crowds; a neural sequence model; adding public datasets as a benchmark for the anomaly stage; automatic retraining.

**Q31. What are the limitations?**
Few congestion onsets, one topology, a flash-crowd false-positive rate, rule-based explanations that are only about 50 % accurate, simulated (not real) traffic.

**Q32. How is reproducibility ensured?**
Fixed seeds, YAML configuration, recorded metadata, pinned dependencies, one command per phase, and automated tests.

**Q33. Why Parquet?**
Compact, typed, fast to read, standard in data science.

**Q34. Why FastAPI + WebSockets?**
FastAPI is fast and self-documenting; WebSockets push new data to the browser without polling.

**Q35. Why separate historical replay and live mode?**
Honesty: recorded demos must never be mistaken for real-time monitoring; replay also allows repeatable demonstrations.

---------------------------------------------------------------------------------------------------------------------

# PART I — Presentation plan (≈ 12–15 minutes)

| # | Slide | Key points | Time |
|---|---|---|---|
| 1 | Title and problem | networks jam; admins need early warning and to separate crowds from attacks | 1 min |
| 2 | Goal and contributions | 3 ML tasks + live dashboard + reproducible pipeline | 1 min |
| 3 | System architecture | the diagram in the README: simulate → measure → label → learn → serve → show | 2 min |
| 4 | Virtual network and scenarios | dumbbell, 10 Mbit/s bottleneck, five scenarios (table) | 1.5 min |
| 5 | What we measure | the 15 metrics and tools (`ip`, `tc`, `ss`, ping, TShark, NFStream, psutil) | 1.5 min |
| 6 | Ground truth | congestion rule, X = 29 ms calibration, 30 s future label | 1.5 min |
| 7 | Avoiding cheating | leakage rules, split by run, first/last 30 s handling | 1 min |
| 8 | Models | classification, 2-stage anomaly detection, congestion prediction + baselines | 1.5 min |
| 9 | Results | the tables in Part F; show honest caveats | 2 min |
| 10 | Live demo | see script below | 3 min |
| 11 | Problems solved | AppArmor, live/offline mismatch (shows rigour) | 1 min |
| 12 | Limitations and future work | few onsets, flash-crowd false positives, one topology | 1 min |
| 13 | Conclusion | what was achieved, what is tentative | 0.5 min |

**Demo script (3 minutes)**
1. Start backend and dashboard (have them already running). Open http://127.0.0.1:5173.
2. *Historical replay →* RAMP **test** run. Press Play at 10×, pause at second ~58: show 95 % probability, warning, then congestion at 60; point out red shading is ground truth.
3. Switch to UDP_FLOOD: explanation "flood-like" with reasons. Switch to FLASH_CROWD: say "this is legitimate, and our first stage flags it — a known limitation".
4. *Model performance tab:* read two numbers (Logistic Regression F1 0.65 vs baseline 0.33) and the caveat.
5. *Live monitoring:* start the real run (`sudo .venv/bin/python -m netguard live ramp --duration 150`), show the dashboard filling in; mention the 30 s warm-up and the 4 s lag. If it cannot be started, play the stored **past live session**.

**Backup plan:** if Mininet cannot start in front of the audience, use *Past session* (it is a real recording of a live run) and say so.

---------------------------------------------------------------------------------------------------------------------

# PART J — Cheat sheet of numbers

| Item | Value |
|---|---|
| Topology | 2 OVS switches, 4 + 4 hosts, 1 bottleneck |
| Bottleneck / access | 10 Mbit/s, 20 ms one-way, 50-packet queue / 100 Mbit/s |
| Scenarios × repetitions × duration | 5 × 3 × 300 s = 15 runs |
| Seeds | 42, 43, 44 |
| Rows / features | 4,500 / 105 |
| Congestion rule | util ≥ 90 % and (queue ≥ X or loss > 1 %) for ≥ 3 s; X = **29 ms** |
| Prediction horizon | 30 s |
| Split | rep 1 train, rep 2 validation, rep 3 test |
| Best classifier | Random Forest 0.908 accuracy |
| Isolation Forest (test) | precision 0.97, recall 1.00, FPR 12.2 %, flags 100 % FLASH_CROWD |
| Best congestion model | Logistic Regression: precision 0.87, recall 0.52, F1 0.65, 5/6 onsets |
| Baseline (util > 80 %) | F1 0.33 |
| Live check | 150 samples, 0 failed, 100 % agreement with offline predictions |
| Tests | ≈ 450 unit + 15 integration |
| Ports | backend 8000, dashboard 5173 |
