# NetGuard AI — Presentation Split for 4 Speakers

Total time: **16 minutes (4 minutes each)**, plus questions. Every number below comes from the project's real results (`results/ml_results.md` and `docs/VIVA_DOCUMENTATION.md`, Part J).

> Note: the slide table in the viva document adds up to about 18.5 minutes, although its title says 12–15. This split trims it to 16. If you only get 12–15 minutes, cut the "Problems solved" parts (marked *optional*) and shorten the demo to 2 minutes.

## The story in one line

> "Networks jam. We built a virtual network, measured it every second, taught three kinds of AI models to recognise traffic, spot anomalies and warn 30 seconds before congestion, and show everything on a live dashboard — and we are honest about what is still tentative."

## Who presents what

| Speaker | Theme | Slides | Time |
|---|---|---|---|
| **Speaker 1** | The problem, the idea and the virtual network | 1, 2, 3, 4 | 4 min |
| **Speaker 2** | The data: what we measure and how we label it honestly | 5, 6, 7 | 4 min |
| **Speaker 3** | The AI models and the results | 8, 9, 12 (ML limits) | 4 min |
| **Speaker 4** | The live system, the demo and the conclusion | 10, 11, 12 (future), 13 | 4 min |

The order follows the pipeline: **simulate → measure → label → learn → serve → show**. Each speaker hands the audience to the next one with a fixed sentence (in bold at the end of each part).

---

# Speaker 1 — The problem, the idea and the virtual network (4 min)

**Owns:** viva Parts A and C1, and the architecture diagram in the README.

## Slide 1 — Title and problem (1 min)
- Networks get congested: queues fill, packets are dropped, delay rises, users suffer.
- An administrator has two problems: **(1)** know *before* it happens; **(2)** tell a harmless crowd from an attack.
- Say: "A sudden jump in traffic can be a popular event or a flood attack. They look similar, and the response is different."

## Slide 2 — Goal and contributions (1 min)
- Three AI tasks: **traffic classification**, **anomaly detection**, **congestion prediction 30 seconds ahead**.
- Plus: a **live dashboard**, and a **reproducible pipeline** (one command per stage, fixed random seeds).
- Say why we built our own data: public datasets do not have per-second queue delay and loss together with a known bottleneck, so we cannot label "congestion" honestly from them.

## Slide 3 — Architecture (1 min)
- Show the README diagram and read it left to right: **simulate → measure → label → features → models → backend → dashboard**.
- One sentence each: Mininet makes the network; collectors measure; rules give the ground truth; scikit-learn / XGBoost learn; FastAPI serves; React shows.

## Slide 4 — Virtual network and scenarios (1 min)
- **Dumbbell topology:** 2 Open vSwitch switches, 4 hosts on each side, one shared **bottleneck of 10 Mbit/s**, 20 ms one-way delay, a 50-packet queue; access links 100 Mbit/s.
- **Five scenarios:** NORMAL, RAMP (load rises step by step), FLASH_CROWD (many legitimate users at once), UDP_FLOOD (attack-like), DEGRADED (extra 60 ms delay and 3 % loss).
- **Experiment size:** 5 scenarios × 3 repetitions × 300 s = **15 runs**, seeds 42, 43, 44.

**Handover:** *"We now have a network that can jam on purpose. My colleague will explain how we measure it and how we know, without cheating, when it is jammed."*

### Speaker 1 — likely questions
- Why simulate instead of using real traffic? → Control: we know the ground truth and can repeat runs.
- Why a bottleneck of only 10 Mbit/s? → It makes congestion easy to create on a laptop-sized VM; the method does not depend on the size.
- Is it only one topology? → Yes. This is a stated limitation.

---

# Speaker 2 — The data: measurement, ground truth and avoiding cheating (4 min)

**Owns:** viva Parts B3, C3, C4, C6, D4, D5, D6 and `docs/FEATURES.md`.

## Slide 5 — What we measure (1.5 min)
- Every **second**, on one shared clock, seven kinds of collectors run: `ip -s link` (throughput, utilisation), `tc -s qdisc` (queue backlog, delay, drops), `ss` (connections, retransmissions), **ping** every 0.2 s (round-trip time, jitter, loss), **TShark** (packets by protocol), **NFStream** (flows), **psutil** (CPU/RAM of the VM).
- 52 raw measurements → **15 base signals** → **105 model features** (current value, 5-second mean/max/std/slope, and 5/10/30-second lags). Result: **4,500 rows**.
- Say: "VM CPU and RAM are measured but are not model inputs. They describe the test computer, not the network, so they would give the models false hints."

## Slide 6 — Ground truth: what "congested" means (1.5 min)
- **Rule:** utilisation ≥ 90 % **and** (queue delay ≥ X **or** loss > 1 %), holding for ≥ 3 seconds in a row.
- **X = 29 ms**, not guessed: it was calibrated from the NORMAL runs (99.9th percentile) and frozen in `data/metadata/calibration.json`.
- **Onset** = first second of an episode. **Label to predict:** "a new congestion onset happens in the next 30 seconds".
- Missing data is never filled with zeros; a second with unknown data stays "unknown".

## Slide 7 — Avoiding cheating (1 min)
- **Split by run, never by row:** repetition 1 = train, 2 = validation, 3 = test. Neighbouring seconds are almost identical, so a random split would leak the answer.
- Features look only at the **past**. The first 30 s of each run have no prediction (not enough history) and are excluded; the last 30 s have no label.
- The scaler is fitted on training data only. The true "currently congested" flag needs the next 2 s, so it is not an input; a past-only `congested_causal` replaces it.
- *Optional (15 s):* **Problem solved —** Ubuntu's AppArmor blocked TShark from writing into our folders (`[Errno 13]`); we found it in the kernel log and captured into a root-owned temp folder.

**Handover:** *"So we have clean, honestly labelled data. Over to my colleague for what the AI learned from it."*

### Speaker 2 — likely questions
- Why X = 29 ms? → Calibrated on NORMAL data, then frozen, so the threshold is not tuned on the results.
- Why three repetitions only? → One-day deadline and a VM with 8 cores; this limits the number of test onsets (see Speaker 3).
- How do you know there is no leakage? → The rules on slide 7, and automatic checks in `netguard/prep/validate.py`.

---

# Speaker 3 — The AI models and the results (4 min)

**Owns:** viva Parts C5, D7, F and `results/ml_results.md`.

## Slide 8 — Models (1.5 min)
- **Traffic classification** (which scenario is this second?): Decision Tree, Random Forest, XGBoost.
- **Anomaly detection, two stages:** Stage 1 Isolation Forest (and LOF for comparison), trained only on NORMAL traffic; the alarm threshold is the 95th percentile of NORMAL validation scores. Stage 2 is a rule-based interpreter that gives a reason ("flood-like", "crowd-like"...).
- **Congestion prediction (next 30 s):** Logistic Regression, Random Forest, XGBoost, compared with **three simple baselines**: persistence, "utilisation > 80 %", and "utilisation trend".
- Fixed settings, no heavy tuning; CPU only.

## Slide 9 — Results on the held-out test runs (2 min)

| Task | Result |
|---|---|
| Classification | Random Forest **0.908** accuracy (XGBoost 0.901, Decision Tree 0.893). UDP_FLOOD is the hardest class (recall 0.60). |
| Anomaly detection | Isolation Forest precision **0.97**, recall **1.00**, but **12.2 % false alarms** on NORMAL. |
| Congestion (not-yet-congested seconds) | Logistic Regression precision **0.87**, recall 0.52, F1 **0.65**, warned before **5 of 6** onsets, mean lead ≈ 30 s. Baseline "utilisation > 80 %": F1 **0.33**. |
| Speed | Logistic Regression ≈ 0.2 ms, XGBoost ≈ 0.4 ms, Random Forest ≈ 100 ms per prediction. |

- Key sentence: "The simple Logistic Regression model beats the baselines, and the persistence baseline cannot warn early at all (recall 0)."
- Most useful features: `nf_active_flows_mean5`, `share_udp`, `rtt_mean_ms_lag30`, `loss_pct_std5`, `rtt_mean_ms_lag5`.

## Slide 12 (ML part) — Honest limits (0.5 min)
- The test set has only **6 congestion onsets** → "promising, not conclusive".
- The anomaly stage flags **100 % of FLASH_CROWD**, which is legitimate traffic; the rule layer agrees with the true scenario only about 50 % of the time (RAMP is called "flood-like").
- The first 30 s have no prediction, so part of the "30 s lead" is warm-up.

**Handover:** *"These models are only useful if someone can see them working in real time. My colleague will show the system and run the demo."*

### Speaker 3 — likely questions
- Why is Logistic Regression better than XGBoost here? → XGBoost is more precise (1.00) but misses more onsets (recall 0.36 vs 0.52). A likely reason is the small number of training positives, which favours a simple model; we did not test this explanation, so say "likely".
- Why is anomaly detection so sensitive? → It is a first-stage filter trained on NORMAL only; anything different, even legitimate, is flagged.
- Is 0.65 F1 good? → It is clearly above the baselines, but with 6 onsets we do not claim a general result.

---

# Speaker 4 — The live system, the demo and the conclusion (4 min)

**Owns:** viva Parts D8–D11, E, G and I (demo script).

## Slide 10 — System and live demo (3 min)
Architecture in one sentence: **FastAPI** backend + SQLite + WebSockets, **React** dashboard on port 5173 (backend on 8000). Historical replay is marked `source: "replay"`; real live data is marked `source: "live"`.

**Demo (rehearse this exactly):**
1. Backend and dashboard are already running. Open `http://127.0.0.1:5173`.
2. *Historical replay →* RAMP **test** run, 10× speed. Pause at about second 58: congestion probability **95 %**, **WARNING**, and the onset comes at second **60**. The red shading is the ground truth.
3. Switch to UDP_FLOOD: explanation "flood-like". Switch to FLASH_CROWD: "this is legitimate; our first stage flags it — a known limitation."
4. *Model performance tab:* Logistic Regression F1 0.65 vs baseline 0.33, and read the caveat.
5. *Live monitoring:* `sudo .venv/bin/python -m netguard live ramp --duration 150`; mention the 30 s warm-up and the ≈ 4 s display lag. If Mininet cannot start, play the stored **past live session** and say it is a real recording.

*Optional (15 s):* **Problem solved —** the first live version disagreed with the offline one (63 % agreement) because "active flows" were counted differently; with the offline definition and a 4 s look-ahead the agreement is now **100 %** (150 samples, 0 failed).

## Slide 12 (future part) — Future work (0.5 min)
- More repetitions and topologies, to get more congestion onsets and a firmer result.
- Teach the first stage to tell a flash crowd from a flood.
- Test against public datasets as an external benchmark.

## Slide 13 — Conclusion (0.5 min)
- We built an end-to-end, reproducible system: virtual network, 15 real runs, 105 features, three model families, a live dashboard, ≈ 450 unit tests and 15 integration tests.
- Warning before 5 of 6 test congestion onsets with a 0.65 F1 beats the baselines, **but** the result is tentative because the test set is small.
- Close: "We report what works, and what does not."

### Speaker 4 — likely questions
- Is the live demo real? → Yes: a real Mininet run feeds the backend every second. Replay is clearly marked as historical.
- Why a 4 s lag in live mode? → To tell which flows are still active, we must see each flow's last packet.
- What if the network is different? → The models were trained on this topology only; a new network needs new data.

---

# Teamwork notes

## Answering questions together
| Topic of the question | Who answers |
|---|---|
| Problem, topology, scenarios, why simulation | Speaker 1 |
| Metrics, tools, labels, calibration, leakage, splits | Speaker 2 |
| Models, metrics, baselines, results, limits | Speaker 3 |
| Backend, dashboard, live mode, tests, demo problems | Speaker 4 |

If a question is not yours, say "my colleague owns that part" and hand over. Do not guess a number: use the cheat sheet.

## Everyone memorises (the 8 numbers)
10 Mbit/s bottleneck · 15 runs · 105 features · 4,500 rows · X = 29 ms · 30 s horizon · RF accuracy 0.908 · Logistic Regression F1 0.65 vs baseline 0.33.

## Rehearsal checklist
1. Run the full presentation once with a timer; each person stops at 4 minutes.
2. Speaker 4 starts backend and dashboard 10 minutes before and checks the past session plays.
3. Speaker 1 switches the slides; speaker 4 owns the browser during the demo.
4. Use the same words for the same things: **onset**, **horizon**, **held-out test runs**, **baseline**.
5. Never say "the model predicts congestion perfectly". Say "promising, based on 6 test onsets".
