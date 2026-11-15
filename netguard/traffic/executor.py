"""Run a :class:`TrafficPlan` on a started Mininet network.

The executor only talks to objects with the small Mininet node interface it
needs (``IP()`` and ``popen()``), so it is unit-tested with fake nodes and
exercised for real by the Mininet integration test.

Isolation: events can only reference hosts of the topology configuration, and
before anything starts every involved host's *actual* IP address is checked to
lie in the private topology subnet. There is no code path that sends traffic to
an address outside the emulated network.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from netguard.config.environment import Environment
from netguard.exceptions import TrafficError
from netguard.traffic.commands import (
    PROJECT_ROOT,
    ServerSpec,
    build_client_command,
    build_server_command,
    iperf3_server_command,
    parse_iperf3_json,
    required_servers,
)
from netguard.traffic.events import TrafficEvent, TrafficPlan
from netguard.traffic.types import Tool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventResult:
    """Outcome of one event. ``started_s`` / ``ended_s`` are offsets from scenario start."""

    event_id: str
    status: str  # "ok" | "failed" | "skipped"
    started_s: Optional[float] = None
    ended_s: Optional[float] = None
    returncode: Optional[int] = None
    detail: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class TrafficExecutor:
    """Schedules and runs the events of a plan; one thread per active event."""

    def __init__(
        self,
        net: Any,
        plan: TrafficPlan,
        env: Environment,
        *,
        python: str = sys.executable or "python3",
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self.net = net
        self.plan = plan
        self.env = env
        self.python = python
        self.root = project_root
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._procs: List[subprocess.Popen] = []
        self._results: List[EventResult] = []
        self._server_procs: List[Any] = []
        self._t0 = 0.0

    # ------------------------------------------------------------------ helpers
    def _ip(self, host: str) -> str:
        return self.env.topology.host_ip(host)

    def _now(self) -> float:
        return time.monotonic() - self._t0

    def _wait_until(self, offset_s: float) -> bool:
        """Sleep until scenario time ``offset_s``; False if stopped meanwhile."""
        return not self._stop.wait(max(0.0, offset_s - self._now()))

    def _popen(self, host: str, argv: List[str]) -> subprocess.Popen:
        node = self.net.get(host)
        logger.debug("[%s] %s", host, " ".join(argv))
        proc = node.popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True)
        with self._lock:
            self._procs.append(proc)
        return proc

    def _record(self, result: EventResult) -> None:
        with self._lock:
            self._results.append(result)

    # ---------------------------------------------------------------- isolation
    def check_isolation(self) -> None:
        """Verify that all hosts used by the plan live inside the private topology subnet."""
        network = self.env.topology.network
        for host in self.plan.hosts_used():
            expected = self._ip(host)
            actual = self.net.get(host).IP()
            if actual != expected:
                raise TrafficError(f"host {host} has IP {actual}, expected {expected}")
            address = ipaddress.ip_address(actual)
            if address not in network or not address.is_private:
                raise TrafficError(f"host {host} IP {actual} is outside the isolated subnet {network}")

    # ------------------------------------------------------------------ servers
    def _start_server(self, spec: ServerSpec) -> None:
        argv = build_server_command(spec, self._ip(spec.host), self.env.traffic, self.python, self.root)
        proc = self._popen(spec.host, argv)
        logger.info("started %s server on %s (%s)", spec.kind, spec.host, self._ip(spec.host))
        self._server_procs.append((spec, proc))

    def _check_servers_alive(self) -> None:
        for spec, proc in self._server_procs:
            if proc.poll() is not None:
                _, err = proc.communicate()
                raise TrafficError(f"{spec.kind} server on {spec.host} exited early: {err.strip()}")

    # ------------------------------------------------------------------- events
    def _run_event(self, event: TrafficEvent) -> None:
        lead = self.env.traffic.executor.server_start_lead_s
        grace = self.env.traffic.executor.grace_s
        server: Optional[subprocess.Popen] = None
        try:
            if event.tool is Tool.IPERF3:
                assert event.port is not None
                server = self._popen(event.dst, iperf3_server_command(event.port))
                if self._stop.wait(lead):
                    return
                if server.poll() is not None:
                    _, err = server.communicate()
                    raise TrafficError(f"iperf3 server on {event.dst}:{event.port} exited: {err.strip()}")
            if not self._wait_until(event.start_s):
                self._record(EventResult(event.event_id, "skipped"))
                return
            argv = build_client_command(event, self._ip(event.dst), self.env.traffic, self.python, self.root)
            started = self._now()
            client = self._popen(event.src, argv)
            try:
                out, err = client.communicate(timeout=event.duration_s + grace)
            except subprocess.TimeoutExpired:
                client.kill()
                client.communicate()
                raise TrafficError(f"client still running {grace:.0f}s after its planned end") from None
            ended = self._now()
            if self._stop.is_set():
                self._record(EventResult(event.event_id, "skipped", started, ended, client.returncode))
                return
            detail = self._parse_output(event, out)
            if client.returncode != 0:
                msg = (err or out or "").strip()[:500]
                self._record(EventResult(event.event_id, "failed", started, ended, client.returncode, detail, msg))
            else:
                self._record(EventResult(event.event_id, "ok", started, ended, 0, detail))
        except Exception as exc:  # noqa: BLE001 - one bad event must not stop the scenario
            logger.error("event %s failed: %s", event.event_id, exc)
            self._record(EventResult(event.event_id, "failed", error=str(exc)))
        finally:
            if server is not None and server.poll() is None:
                server.terminate()

    @staticmethod
    def _parse_output(event: TrafficEvent, out: str) -> Optional[Dict[str, Any]]:
        try:
            if event.tool is Tool.IPERF3:
                r = parse_iperf3_json(out)
                return {k: v for k, v in r.__dict__.items() if v is not None}
            if event.tool in (Tool.HTTP, Tool.DNS):
                return json.loads(out.strip().splitlines()[-1])
        except (TrafficError, ValueError, IndexError):
            return None
        return None

    # --------------------------------------------------------------------- run
    @property
    def t0_monotonic(self) -> float:
        """``time.monotonic()`` value of scenario time 0 (valid once :meth:`run` has started)."""
        return self._t0

    def run(self, t0_monotonic: Optional[float] = None) -> List[EventResult]:
        """Execute the whole plan (blocking). Returns one result per event.

        ``t0_monotonic`` pins scenario time 0 to a ``time.monotonic()`` instant chosen by the
        caller (the Phase 2 orchestrator uses it to align the collectors); by default time 0 is
        shortly after the servers have been started.
        """
        self.check_isolation()
        executor = self.env.traffic.executor
        threads: List[threading.Thread] = []
        try:
            for spec in required_servers(self.plan):
                self._start_server(spec)
            self._stop.wait(executor.server_start_lead_s)
            self._check_servers_alive()

            lead = executor.server_start_lead_s
            if t0_monotonic is None:
                self._t0 = time.monotonic() + lead  # iperf3 servers for t=0 events start before it
            else:
                self._t0 = t0_monotonic
            logger.info("running %d events over %.0f s", len(self.plan.events), self.plan.duration_s)
            for event in sorted(self.plan.events, key=lambda e: e.start_s):
                if not self._wait_until(event.start_s - lead):
                    break
                thread = threading.Thread(target=self._run_event, args=(event,), name=event.event_id, daemon=True)
                thread.start()
                threads.append(thread)
            deadline = time.monotonic() + (self.plan.duration_s - self._now()) + executor.grace_s + 5
            for thread in threads:
                thread.join(max(0.0, deadline - time.monotonic()))
        finally:
            self.stop()
            for thread in threads:
                thread.join(5)
        results = self.results()
        failed = sum(1 for r in results if r.status == "failed")
        logger.info("finished: %d ok, %d failed, %d skipped", sum(r.status == "ok" for r in results), failed,
                    sum(r.status == "skipped" for r in results))
        return results

    def results(self) -> List[EventResult]:
        with self._lock:
            done = {r.event_id: r for r in self._results}
        # Events that never ran (e.g. interrupted) are reported as skipped, never invented as ok.
        return [done.get(e.event_id, EventResult(e.event_id, "skipped")) for e in self.plan.events]

    def stop(self) -> None:
        """Abort pending events and terminate every process started by this executor."""
        self._stop.set()
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
