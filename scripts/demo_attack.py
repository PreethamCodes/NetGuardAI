#!/usr/bin/env python3
"""Lightweight "attack vs. ML detection" demo for NetGuard AI.

What it does, with code already in this repository:

1. Writes a tiny demo website and hosts it, with a small custom HTTP server, on
   Mininet host ``h5`` - the same host the ``udp_flood`` scenario already floods
   (see ``configs/scenarios/udp_flood.yaml``). From t=120s to t=180s (the
   flood's own window) it serves a clear, hardcoded 503 error page instead of
   the normal page - a guaranteed-visible "the site is down", rather than
   relying on the flood to coincidentally make a trivial page slow.
2. Probes that website once a second from host ``h2`` with ``curl`` and logs
   the response time/status, so you can *watch the site struggle* in a
   terminal (``tail -f /tmp/netguard_demo/site_probe.log``).
3. Runs the real ``udp_flood`` scenario inside Mininet: light legitimate
   background traffic (TCP, HTTP, DNS) starts immediately; a real 20 Mbit/s
   UDP flood from ``h1`` to ``h5`` - twice the 10 Mbit/s bottleneck - fires
   automatically at t=120s and lasts 60s.
4. Streams every second's *real* measurements to the backend
   (``python -m netguard serve``, already running), which scores them with
   the project's own best trained models (XGBoost classifier, Isolation
   Forest anomaly detector, Logistic Regression congestion predictor -
   see ``results/ml_results.md``).

Nothing here is simulated after the fact or hardcoded as "detected": every
number the dashboard shows is the real model's output on the real traffic
this script generates.

Two ways to run it, for a two-part demo:

    # Part 1 - show the site fail, undefended:
    sudo .venv/bin/python scripts/demo_attack.py --duration 200

    # Part 2 - show it detected AND mitigated: the first second the model flags the flood as
    # flood-like/congestion, the attacker's link is rate-limited for real with `tc` (see
    # _mitigating_poster below) and the website's 503 error page is lifted immediately,
    # well before the flood's own window (t=120-180s) ends on its own.
    sudo .venv/bin/python scripts/demo_attack.py --duration 200 --mitigate

Then, on the laptop/VM desktop:

    * open demo/dashboard.html in a browser  (the ML detection view)
    * in a second terminal: tail -f /tmp/netguard_demo/site_probe.log   (the website's health)

See docs/DEMO_GUIDE.md for the full script and talking points.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from netguard.config.loader import DEFAULT_CONFIG_DIR, load_environment  # noqa: E402
from netguard.exceptions import NetGuardError  # noqa: E402
from netguard.experiments.calibration import effective_labeling  # noqa: E402
from netguard.experiments.cli import DEFAULT_CAMPAIGN  # noqa: E402
from netguard.experiments.collectors.livepackets import LivePacketCollector  # noqa: E402
from netguard.experiments.config import ExperimentConfig, load_experiment_config, load_labeling  # noqa: E402
from netguard.experiments.livefeed import LiveFeeder  # noqa: E402
from netguard.experiments.orchestrator import ExperimentOrchestrator, RunSpec, _Runtime  # noqa: E402
from netguard.utils.system import is_linux, is_root  # noqa: E402

DEMO_DIR = Path("/tmp/netguard_demo")
SITE_PORT = 8081  # different from the scenario's own http-browse port (8000), to avoid any clash

# Must match the udp_flood component in configs/scenarios/udp_flood.yaml (start_s / duration_s).
FLOOD_START_S = 120.0
FLOOD_END_S = 180.0

# A small, self-contained HTTP server (no string formatting applied to it - every dynamic value is
# passed as a command-line argument, not baked into this source) that deliberately serves a clear
# "503" error page for the duration of the scenario's flood - a hardcoded, guaranteed-visible stand-in
# for "the site is down", rather than relying on the flood to coincidentally make a trivial page slow.
# If a "mitigated" flag file exists (written by _mitigating_poster the moment the model catches the
# flood), the error page is lifted immediately, even while the flood is still technically running.
SITE_SERVER_SCRIPT = r"""
import http.server, socketserver, os, sys, time

PORT, BIND, START, FLOOD_START, FLOOD_END, FLAG = (
    int(sys.argv[1]), sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]), sys.argv[6])

OK_HTML = b'''<!doctype html>
<html><head><meta charset="utf-8"><title>QuickCart Demo Store</title>
<style>
 body{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:40px;text-align:center}
 h1{color:#38bdf8;margin-bottom:4px} .tag{color:#94a3b8;margin-top:0}
 .card{background:#1e293b;border-radius:12px;padding:30px;max-width:420px;margin:30px auto;
       box-shadow:0 4px 18px rgba(0,0,0,.4)}
 .price{color:#4ade80;font-size:28px;font-weight:bold}
 button{background:#38bdf8;border:none;padding:10px 24px;border-radius:6px;font-size:16px;cursor:pointer;
        margin-top:16px;color:#0f172a;font-weight:bold}
 .note{color:#64748b;font-size:13px;max-width:420px;margin:24px auto}
</style></head>
<body>
 <h1>QuickCart Demo Store</h1>
 <p class="tag">a sample website, served from inside the test network (host h5)</p>
 <div class="card">
   <h2>Wireless Headphones</h2>
   <p class="price">$49.99</p>
   <button onclick="alert('Demo page only - no real checkout.')">Buy now</button>
 </div>
 <p class="note">Served by NetGuard AI's attack demo (scripts/demo_attack.py).</p>
</body></html>
'''

ERROR_HTML = b'''<!doctype html>
<html><head><meta charset="utf-8"><title>503 Service Unavailable</title>
<style>
 body{font-family:Arial,sans-serif;background:#1c0a0a;color:#fecaca;margin:0;padding:60px 20px;text-align:center}
 h1{color:#f87171;font-size:48px;margin:0} h2{color:#fca5a5;font-weight:normal;margin-top:8px}
 .box{background:#2a0f0f;border:1px solid #7f1d1d;border-radius:12px;padding:30px;max-width:480px;margin:28px auto}
 code{background:#450a0a;padding:2px 6px;border-radius:4px}
 .note{color:#f3a7a7;font-size:13px;max-width:480px;margin:20px auto}
</style></head>
<body>
 <h1>503</h1>
 <h2>Service Unavailable</h2>
 <div class="box">
   <p>QuickCart Demo Store cannot handle this request right now.</p>
   <p><code>Reason: upstream link saturated (unusually high incoming traffic)</code></p>
 </div>
 <p class="note">This page is served by NetGuard AI's attack demo to make the website's failure clearly
 visible. It is lifted automatically once the attack is mitigated.</p>
</body></html>
'''


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        elapsed = time.time() - START
        failing = (FLOOD_START <= elapsed < FLOOD_END) and not os.path.exists(FLAG)
        body, status = (ERROR_HTML, 503) if failing else (OK_HTML, 200)
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


with Server((BIND, PORT), Handler) as httpd:
    httpd.serve_forever()
"""

PROBE_SCRIPT = """#!/bin/bash
# Probes the demo website once a second and logs its status + response time.
URL="$1"
LOG="$2"
while true; do
  ts=$(date +%H:%M:%S)
  out=$(curl -s -o /dev/null -m 2 -w "%{http_code} %{time_total}s" "$URL" 2>/dev/null)
  if [ -z "$out" ]; then
    echo "[$ts] GET $URL -> TIMEOUT / UNREACHABLE (>2s)" >> "$LOG"
  else
    echo "[$ts] GET $URL -> $out" >> "$LOG"
  fi
  sleep 1
done
"""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario", nargs="?", default="udp_flood",
                    help="scenario to run (default: udp_flood - background traffic, then a real UDP flood "
                         "against the demo website's host at t=120s for 60s)")
    p.add_argument("--duration", type=float, default=200.0,
                    help="must be >= the flood's start (120s) + length (60s) + a margin; default 200")
    p.add_argument("--seed", type=int, default=7, help="traffic seed")
    p.add_argument("--backend", default="http://127.0.0.1:8000", help="URL of the running backend")
    p.add_argument("--stream-id", default=None, help="name of this live session (default: live-attack-<time>)")
    p.add_argument("--server-host", default="h5", help="Mininet host that serves the demo website (the flood's victim)")
    p.add_argument("--client-host", default="h2", help="Mininet host that probes the website every second")
    p.add_argument("--attacker-host", default="h1", help="Mininet host the udp_flood scenario floods from")
    p.add_argument("--mitigate", action="store_true",
                    help="when the backend's own model flags the flood, automatically rate-limit the attacker "
                         "(tc on the attacker's link) - run once WITHOUT this flag to show the site fail, then "
                         "again WITH it to show detection + mitigation")
    p.add_argument("--mitigate-rate-mbps", type=float, default=1.0,
                    help="rate the attacker is throttled to once mitigation fires (default 1 Mbit/s)")
    p.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR))
    return p


def _mitigating_poster(net, attacker_host: str, rate_mbps: float, mitigation_log: Path, mitigated_flag: Path):
    """Posts each sample like ``netguard.experiments.livefeed.http_post``, but also reads the backend's own
    prediction back and, the first time it flags the flood, rate-limits the attacker's link with ``tc`` -
    a real countermeasure applied because the model detected it, not a scripted effect. Also touches
    ``mitigated_flag`` so the demo website's own server (which only knows a fixed time window) lifts its
    hardcoded error page immediately, instead of waiting for the whole flood window to end."""
    state = {"applied": False}

    def poster(url: str, payload: dict, timeout: float = 3.0) -> int:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local backend URL
            status = resp.status
            body = json.loads(resp.read().decode())
        anomaly = (body.get("predictions") or {}).get("anomaly") or {}
        if not state["applied"] and anomaly.get("flagged") and anomaly.get("kind") in ("flood-like", "congestion"):
            state["applied"] = True
            iface = f"{attacker_host}-eth0"
            net.get(attacker_host).cmd(f"tc qdisc replace dev {iface} root tbf rate {rate_mbps}mbit "
                                      f"burst 32kbit latency 400ms")
            mitigated_flag.touch()
            line = (f"*** MITIGATION APPLIED (second {payload.get('second')}): detected "
                   f"{anomaly.get('kind')!r} (anomaly score {anomaly.get('score', 0):.2f}) "
                   f"-> rate-limited {attacker_host} to {rate_mbps} Mbit/s, website error page lifted ***")
            print("\n" + line + "\n")
            with mitigation_log.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return status

    return poster


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if not is_linux() or not is_root():
        raise NetGuardError("run this with sudo inside the Ubuntu VM (needs Mininet root privileges)")
    try:
        urllib.request.urlopen(args.backend.rstrip("/") + "/api/health", timeout=3).read()
    except (urllib.error.URLError, OSError) as exc:
        raise NetGuardError(f"backend not reachable at {args.backend} ({exc}); start it first with: "
                            f"python -m netguard serve") from exc

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    site_server_path = DEMO_DIR / "site_server.py"
    site_server_path.write_text(SITE_SERVER_SCRIPT, encoding="utf-8")
    probe_path = DEMO_DIR / "probe.sh"
    probe_path.write_text(PROBE_SCRIPT, encoding="utf-8")
    probe_path.chmod(0o755)
    probe_log = DEMO_DIR / "site_probe.log"
    probe_log.write_text("", encoding="utf-8")  # fresh log for this run
    http_log = DEMO_DIR / "http_server.log"
    mitigation_log = DEMO_DIR / "mitigation.log"
    mitigation_log.write_text("", encoding="utf-8")
    mitigated_flag = DEMO_DIR / "mitigated.flag"
    mitigated_flag.unlink(missing_ok=True)  # fresh run: the error page must show again until re-mitigated

    main_cfg = load_experiment_config(DEFAULT_CAMPAIGN)
    live_dir = main_cfg.output.data_dir / "live"
    cfg = ExperimentConfig.from_mapping({
        "name": "live-attack-demo", "scenarios": [args.scenario], "repetitions": 1, "seeds": [args.seed],
        "duration_s": args.duration, "timeline": {"pre_roll_s": 5, "tail_s": 7},
        "output": {"data_dir": str(live_dir), "results_dir": str(live_dir / "results")},
    })
    stream_id = args.stream_id or f"live-attack-{time.strftime('%H%M%S')}"
    labeling = effective_labeling(load_labeling(args.config_dir), main_cfg.output.data_dir)

    class _DemoFeeder:
        """The normal LiveFeeder, plus starting/stopping the demo website and its prober."""

        def __init__(self, feeder: LiveFeeder, net, server_host: str, client_host: str) -> None:
            self._feeder, self._net, self._server_host, self._client_host = feeder, net, server_host, client_host

        def start(self) -> None:
            self._feeder.start()

        def stop(self) -> None:
            self._feeder.stop()
            self._net.get(self._server_host).cmd(f"pkill -f {site_server_path}")
            self._net.get(self._client_host).cmd(f"pkill -f {probe_path}")

        def __getattr__(self, name):  # forwards .summary()/.samples/.posted/.last_error to the real feeder
            return getattr(self._feeder, name)

    def factory(timeline, collectors, net, env, capture_dev):
        span = cfg.timeline.pre_roll_s + timeline.duration_s + cfg.timeline.tail_s + 5
        packets = LivePacketCollector(capture_dev, timeline.anchor_wall, env.traffic.http.port, span)
        packets.start()
        collectors["live_packets"] = packets

        server, client = net.get(args.server_host), net.get(args.client_host)
        server_ip = server.IP()
        server.cmd(f"nohup python3 {site_server_path} {SITE_PORT} {server_ip} {timeline.anchor_wall} "
                  f"{FLOOD_START_S} {FLOOD_END_S} {mitigated_flag} > {http_log} 2>&1 &")
        time.sleep(0.3)
        client.cmd(f"nohup bash {probe_path} http://{server_ip}:{SITE_PORT}/ {probe_log} > /dev/null 2>&1 &")
        print(f"demo website: http://{server_ip}:{SITE_PORT}/  (served on {args.server_host}, probed every 1s "
             f"from {args.client_host} -> {probe_log}); it serves a hardcoded 503 error page from t="
             f"{FLOOD_START_S:g}s to t={FLOOD_END_S:g}s unless mitigated")

        feeder_kwargs = {}
        if args.mitigate:
            feeder_kwargs["poster"] = _mitigating_poster(net, args.attacker_host, args.mitigate_rate_mbps,
                                                         mitigation_log, mitigated_flag)
            print(f"mitigation ARMED: once the model flags the flood as flood-like/congestion, {args.attacker_host} "
                 f"will be rate-limited to {args.mitigate_rate_mbps} Mbit/s and the website's error page lifted")
        else:
            print("mitigation OFF: the site will show its 503 error page for the whole flood, undefended")

        raw_feeder = LiveFeeder(timeline, collectors, backend_url=args.backend, stream_id=stream_id,
                                capacity_mbps=env.links.bottleneck.bandwidth_mbps, probe_interval_s=cfg.probe.interval_s,
                                **feeder_kwargs)
        feeder = _DemoFeeder(raw_feeder, net, args.server_host, args.client_host)
        feeder.start()
        print(f"live session {stream_id!r}: streaming to {args.backend} -> open demo/dashboard.html")
        return feeder

    orch = ExperimentOrchestrator(cfg, load_environment(args.config_dir), labeling, Path(args.config_dir),
                                  _Runtime(live_factory=factory))
    print(f"scenario {args.scenario!r}: background traffic starts now; the UDP flood against "
         f"{args.server_host} fires automatically at t=120s and lasts 60s (20 Mbit/s, 2x the bottleneck).")
    meta = orch.run_experiment(RunSpec(args.scenario, 1, args.seed))
    print(f"experiment {meta.experiment_id}: {meta.status}")
    live = meta.checks.get("live", {})
    print(f"samples posted {live.get('posted')}, failed {live.get('failed')}")
    for e in meta.errors[:3]:
        print("ERROR:", e[:300])
    return 0 if meta.status == "success" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NetGuardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
