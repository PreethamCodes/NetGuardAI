# Live attack demo — "attack the demo website, watch our AI catch it"

A lightweight, self-contained demo built entirely from code already in this repo. It does three (or
four, with mitigation on) real things, not staged ones:

1. Hosts a tiny sample website (`QuickCart Demo Store`) on Mininet host **h5**. For the scenario's own
   flood window (**t = 120s to t = 180s**) it serves a clear, hardcoded `503 Service Unavailable` error
   page instead of the normal one - a guaranteed-visible "the site is down", rather than hoping the
   flood happens to make a trivial static page slow enough to notice.
2. Runs the project's own `udp_flood` scenario: light legitimate traffic starts immediately; a real
   **20 Mbit/s UDP flood from h1 → h5** (twice the 10 Mbit/s bottleneck) fires automatically at
   **t = 120 s** and lasts **60 s**.
3. Streams every second's real measurements to the backend, which scores them with the project's
   **already-trained best models** — XGBoost classifier, Isolation Forest anomaly detector, Logistic
   Regression congestion predictor (see `results/ml_results.md`) — and a new, small standalone
   dashboard (`demo/dashboard.html`) shows the detection live.
4. Optionally (`--mitigate`), the moment the model flags the flood, rate-limits the attacker for real
   with `tc` — so the same script can show either "detected, nothing done" or "detected and mitigated".

Nothing is hardcoded as "detected" — the dashboard only ever shows what the real models output on the
real traffic the script generates. The one thing that *is* fixed in advance, for a repeatable live
demo, is *when* the flood starts (t = 120 s into the run) — see "If you want to trigger it yourself" below.

## Why h5 and not a plain laptop website

The trained models expect the 105 features measured inside this project's own simulated network (a
10 Mbit/s bottleneck, Mininet's `tc`/`ip`/`ss` counters, etc.). A website run directly on the laptop's
real NIC (1 Gbit/s+, no controlled bottleneck) would not match what the models were trained on, so
their output would be meaningless. Hosting the demo site on a host *inside* the same virtual network
the models were trained on is what makes the detection real instead of decorative.

## One-time setup (already true in this repo — just confirm)

```bash
cd ~/Desktop/netguardAI/NetGuardAI
ls models/*.joblib          # trained models already exist (Phase 4/5)
```

## Running the demo

**Terminal 1 — backend** (if not already running):
```bash
cd ~/Desktop/netguardAI/NetGuardAI
.venv/bin/python -m netguard serve
```

**Terminal 2 — the attack + website + probe, all in one** (needs root, inside the Ubuntu VM). Run this
**twice**, once per half of the story:

```bash
cd ~/Desktop/netguardAI/NetGuardAI

# Part 1 - show the site fail, undefended:
sudo .venv/bin/python scripts/demo_attack.py --duration 200
```

```bash
# Part 2 - show it detected AND mitigated. The moment the backend's own model flags the flood
# as flood-like/congestion, the script rate-limits the attacker (h1) for real, with `tc`, on
# its own - this is a real countermeasure triggered by a real detection, not a scripted effect.
sudo .venv/bin/python scripts/demo_attack.py --duration 200 --mitigate
```

Either way, it prints the demo website's URL and the probe log path, then runs for ~200 s. Background
traffic starts immediately; the flood starts at t ≈ 120 s and lasts 60 s. In Part 2, watch the terminal
for a line starting `*** MITIGATION APPLIED ***` once the flood is detected (also logged to
`/tmp/netguard_demo/mitigation.log`), and watch Terminal 3 recover afterwards even though the flood is
still running.

The website's `503` page is on a fixed timer (t=120s–180s), not something inferred from real measurements
of the site - it is the one deliberately "hardcoded" part of the demo, so the failure is always clearly
visible regardless of exactly how hard the flood actually hits a trivial page. In Part 2, the moment
mitigation fires it touches `/tmp/netguard_demo/mitigated.flag`, which the website checks on every
request - so the error page can disappear well before t=180s, demonstrating the fix, not just a timer
running out.

**Terminal 3 — watch the website struggle:**
```bash
tail -f /tmp/netguard_demo/site_probe.log
```
Before the flood you'll see `200 0.0Xs` once a second. From t ≈ 120 s every line turns into
`503 0.0Xs` (the website's own hardcoded error page, served for the whole flood window unless
mitigated) - occasionally you may also see `TIMEOUT / UNREACHABLE` if the link is too saturated for
even the error page to get through in time.

**Browser — the ML detection dashboard:**
If the main frontend is running (`npm run dev` inside `frontend/`), just open it at
`http://localhost:5173` and click the **"Live attack demo"** tab in its top nav — the same dashboard,
embedded, so you never have to leave the app or juggle browser tabs during class. Without the frontend
running, open [`demo/dashboard.html`](../demo/dashboard.html) directly instead (double-click the file,
or `file:///…/NetGuard_AI/demo/dashboard.html`) — it connects straight to the backend's WebSocket
(`ws://127.0.0.1:8000/ws/live`), no build step or dev server required. Either way it will say
"Waiting for a live session…" until Terminal 2 is running.

## What you'll see, in order

| ~Time | Website probe (Terminal 3) | Dashboard |
|---|---|---|
| t = 0–30 s | `200 0.0Xs` every second | "Warming up…" (models need 30 s of history) |
| t = 30–120 s | `200 0.0Xs`, steady | Banner **Normal**; traffic classified `NORMAL`; utilisation low |
| t ≈ 120 s | `503` starts appearing every second | Utilisation line jumps toward 100%; classification shifts to `UDP_FLOOD` |
| t ≈ 121–180 s | `503` every second (Part 1) | Banner **⚠ ATTACK DETECTED — congestion imminent** (anomaly flagged *flood-like*, congestion probability high); alerts list fills in |
| t ≈ 180 s+ | back to `200 0.0Xs` | Banner returns to **Normal** as the flood ends |

In **Part 2** (`--mitigate`), the terminal running the script prints `*** MITIGATION APPLIED ***` a few
seconds after t ≈ 120 s (the first second the model flags `flood-like`/`congestion`), and from then on
Terminal 3 should recover to `200 0.0Xs` almost immediately — not at t ≈ 180 s — because the website
checks the `mitigated.flag` file on every request and lifts its error page the instant it appears.

## If you want to trigger the attack yourself, interactively

The simplest, most reliable option for a live audience is the one above: start the whole session and
narrate "...and now, two minutes in, the attack starts" right as it does — it is a real flood, just
timed in advance so nothing can go wrong on stage. If you'd rather press a key at the moment of your
choosing, you have two lower-effort options instead of changing the script:

* Shorten the wait: `--duration 200` only controls the demo's own run length — the flood's start time
  (120 s) comes from `configs/scenarios/udp_flood.yaml`. Copy that file to
  `configs/scenarios/udp_flood_quick.yaml` and change `start_s: 120` to something shorter (e.g. `20`),
  then run `sudo .venv/bin/python scripts/demo_attack.py udp_flood_quick --duration 90`.
* Rehearse the timing once beforehand with a stopwatch — 120 s is easy to narrate to ("in two minutes…").

## Cleaning up

`scripts/demo_attack.py` stops the website server and the prober itself when the run ends or is
interrupted (Ctrl+C), as part of the normal Mininet teardown; the `--mitigate` rate-limit on `h1` goes
away with it too, since the whole virtual network (and its `tc` rules) is torn down. If a stray process
is ever left behind:
```bash
sudo pkill -f "http.server 8081"
sudo pkill -f netguard_demo/probe.sh
```

`/tmp/netguard_demo/mitigation.log` records exactly when (and why) mitigation fired, if you want a
second artifact to show alongside `site_probe.log`.

## Talking points (for the viva)

* "This is not a recorded replay — it's a live Mininet run, with a real website and a real flood,
  scored second-by-second by the same models in `results/ml_results.md`."
* "The anomaly detector (Isolation Forest) has recall 1.00 on non-normal test seconds — it is built to
  never miss an unusual second, at the cost of some false alarms on legitimate traffic, which the demo
  does not need to hide."
* "The congestion predictor's warning fires using only *past* data — it is the same model that warned
  before 5 of the 6 held-out test onsets, about 30 seconds early."
* "In Part 1, nothing responds to the flood — the site is left to struggle, so you can see what
  'detection without a response' looks like."
* "In Part 2, the mitigation is triggered by the model's own output, not a timer: the script watches
  the prediction that comes back from every `/api/ingest` call, and the instant it sees `flood-like` or
  `congestion` flagged, it rate-limits the attacker with a real `tc` command on its interface."
* "Normally NetGuard AI only recommends and never changes the network itself (see
  `/api/recommendations`) — Part 2's auto-mitigation is a demo-only addition on top of that, to show
  what an automated response *could* look like once a human approves it."
