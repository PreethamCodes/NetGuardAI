# NetGuard AI — Presentation Topics for 4 Speakers

Total: **~20 minutes (about 5 minutes each)**, plus questions. The split follows the project pipeline
(**network → measurement → models → live system**) and gives every speaker a **different** set of Computer
Networks and ML topics, so nobody repeats anyone. All numbers are from the project's real results
(`results/ml_results.md`).

## At a glance

| Speaker | Theme | Main topics | 3D-view scenes to show |
|---|---|---|---|
| **1** | The problem, the network and the traffic | Real-world problem · pipeline architecture · dumbbell topology · bandwidth/delay/loss · IP addressing · TCP vs UDP · HTTP/DNS/ICMP · the 5 scenarios | Experiment topology · Handshake vs. SYN flood |
| **2** | How congestion is created, measured and labelled | netem + tbf · token bucket · drop-tail queue · bufferbloat · MTU/offloads · the 7 collectors · active vs passive · flows (5-tuple) · congestion rule · X = 29 ms · leakage-free splits | Queue & packet drop · Congestion building up |
| **3** | The machine learning | 105 features and how they're built · top-12 features · the 3 jobs · why XGBoost / Isolation Forest / Logistic Regression · results and honest limits | Isolation Forest · Logistic Regression · Traffic classification |
| **4** | The live system, the demo and the future | Backend and dashboard · live pipeline · **attack + mitigation demo (Parts 1 and 2)** · problems solved · limitations · future work (SDN, ECN/AQM, QUIC, IPv6) | Live attack-demo topology · Attack & mitigation |

---

## Speaker 1 — The problem, the network and the traffic (~5 min)

1. **The real-world problem:** networks jam suddenly; a crowd and a flood attack look alike; admins need a warning
   *before*, not after.
2. **What we built (one sentence per stage):** simulate → measure → label → learn → serve → show.
3. **The virtual network:** dumbbell topology (h1–h4 ↔ s1 ═ s2 ↔ h5–h8), 100 Mbit/s access links, one 10 Mbit/s
   bottleneck, 20 ms one-way delay → ~40 ms RTT. IPv4 `10.0.0.0/24` addressing.
4. **Transport and application protocols:** TCP (handshake, retransmission, congestion control) vs UDP (none — which is
   why floods are UDP). HTTP, DNS and ICMP traffic we generate.
5. **The 5 scenarios:** NORMAL, RAMP, FLASH_CROWD, UDP_FLOOD, DEGRADED — 5 × 3 runs × 300 s.

*Docs:* `docs/cn_concepts/01-topology-and-links.md` and the first half of `02-switching-and-transport.md`.
**Handover:** *"We now have a network that can jam on purpose. Next: how we make it jam, measure it, and know for
certain when it's congested."*

## Speaker 2 — Traffic control, measurement and ground truth (~5 min)

1. **How congestion is created:** `netem` (delay/loss) + `tbf` (token bucket) on every link; drop-tail queue —
   50 packets ≈ 60 ms; bufferbloat; why we disable TSO/GSO/GRO; 1472-byte packets avoid fragmentation.
2. **How it's measured:** the 7 collectors (`ip`, `tc`, `ss`, ping, TShark, NFStream, psutil) mapped to real monitoring
   ideas; active vs passive measurement; flows and the 5-tuple (NetFlow/IPFIX); one shared clock; RFC 3550 jitter.
3. **How it's labelled:** congested = utilisation ≥ 90 % AND (queue delay ≥ X **or** loss > 1 %) for 3 s; **X = 29 ms**
   calibrated from NORMAL runs; congestive loss vs random loss (the DEGRADED scenario).
4. **No cheating:** split by run (rep 1 train / 2 validation / 3 test), past-only features, first 30 s excluded.

*Docs:* `docs/cn_concepts/03-queueing-and-congestion.md` and `04-measurement-and-monitoring.md`.
**Handover:** *"Clean, honestly labelled data. My colleague will show what the models learned from it."*

## Speaker 3 — The machine learning (~5 min)

1. **The 105 features:** 15 raw "vitals" → 5-second mean/max/std/slope → 5/10/30-second look-backs → a past-only
   congestion flag; protocol shares.
2. **The top 12:** `nf_active_flows_mean5`, `share_udp`, `loss_pct_std5`, the four RTT variants, `share_dns`,
   `nf_new_flows_mean5`, `share_icmp`, … — and why delay is the earliest symptom.
3. **Three jobs, three models, and why:**
   - Classification → **XGBoost** (RF is 0.7 pt more accurate, ~230× slower).
   - Anomaly detection → **Isolation Forest** (precision 0.97, recall 1.00; ~12 % false alarms).
   - Congestion prediction → **Logistic Regression** (F1 0.65 vs baseline 0.33; warned before 5 of 6 onsets).
4. **Honest limits:** only 6 test congestion onsets; the anomaly stage flags legitimate FLASH_CROWD; the rule layer agrees
   with the true scenario ~50 % of the time.

*Docs:* `docs/ml-explanations/01-models.md`, `02-features.md`, `docs/FEATURES.md`.
**Handover:** *"Models are only useful if someone can see them working. Over to the live system and the demo."*

## Speaker 4 — The live system, the demo and the future (~5 min)

1. **The system:** FastAPI backend + WebSocket, the 4-tab dashboard, live pipeline (live-vs-offline agreement 100 %).
2. **Demo Part 1 — the site fails:** `sudo .venv/bin/python scripts/demo_attack.py --duration 200` → real UDP flood →
   the sample website serves a `503` page; dashboard shows **ATTACK DETECTED**.
3. **Demo Part 2 — detected and mitigated:** same command with `--mitigate` → the model flags the flood, the attacker is
   rate-limited with `tc`, the `503` lifts within seconds.
4. **Problems we solved (pick one):** AppArmor blocking TShark (`[Errno 13]`), or the live/offline flow-definition mismatch.
5. **Future work:** SDN/OpenFlow controller for network-level mitigation, ECN + CoDel instead of drop-tail, QUIC/TLS traffic,
   IPv6, true multi-source DDoS. **Conclusion:** what works, what is tentative.

*Docs:* `docs/RUN_GUIDE.md` §8 and §10, `docs/DEMO_GUIDE.md`, `docs/cn_concepts/05-future-work.md`.

---

## Who answers which question

| Topic of the question | Answered by |
|---|---|
| Problem, topology, IP addressing, TCP/UDP, scenarios | Speaker 1 |
| Queueing, tc, measurement tools, flows, labels, calibration, leakage | Speaker 2 |
| Features, models, metrics, baselines, limits | Speaker 3 |
| Backend, dashboard, live mode, the attack demo, future work | Speaker 4 |

If it isn't yours: *"my colleague owns that part"* — and hand over. Never guess a number.

## Everyone memorises these 8 numbers

10 Mbit/s bottleneck · 15 runs · 4,500 rows × 105 features · X = 29 ms · 30 s horizon · RF 0.908 accuracy ·
Logistic Regression F1 0.65 vs 0.33 baseline · 5 of 6 onsets warned.

## Demo logistics (Speaker 4 leads)

1. Ten minutes before: start the backend and the dashboard (`docs/RUN_GUIDE.md` §3); open the **Live attack demo** tab.
2. Keep a terminal ready for `tail -f /tmp/netguard_demo/site_probe.log` so the audience sees `503`s appear.
3. Backup if Mininet won't start: play a stored **past live session** from the Monitoring tab and say it's a recording.
4. Speaker 1 drives the slides; Speaker 4 owns the browser and terminal during the demo.
5. Ready-made decks are in `docs/presentations/`.
