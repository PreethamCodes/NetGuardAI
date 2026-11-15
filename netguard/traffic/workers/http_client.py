"""Paced HTTP client: ``--clients`` threads fetch ``--url`` at ``--rate`` requests/s in total.

Every request opens a new TCP connection, so the connection rate equals the
request rate (this is what makes a flash crowd visible in connection counts).
Prints a JSON summary on exit.
"""

from __future__ import annotations

import argparse
import http.client
import random
from typing import Optional, Sequence
from urllib.parse import urlsplit

from netguard.traffic.workers.common import emit_summary, run_paced_clients


def fetch(url: str, timeout: float) -> int:
    """GET ``url`` on a fresh connection and return the body length."""
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname:
        raise ValueError(f"only plain http URLs are supported: {url!r}")
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        conn.request("GET", parts.path or "/")
        response = conn.getresponse()
        body = response.read()
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return len(body)
    finally:
        conn.close()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", required=True)
    p.add_argument("--rate", type=float, required=True, help="aggregate requests per second")
    p.add_argument("--duration", type=float, required=True, help="seconds")
    p.add_argument("--clients", type=int, default=1, help="parallel client threads")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--timeout", type=float, default=5.0)
    args = p.parse_args(argv)
    if args.rate <= 0 or args.duration <= 0 or args.clients < 1:
        p.error("--rate and --duration must be > 0 and --clients >= 1")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    def action(_idx: int, _rng: random.Random) -> int:
        return fetch(args.url, args.timeout)

    stats = run_paced_clients(action, rate=args.rate, clients=args.clients, duration=args.duration, seed=args.seed)
    return emit_summary(stats, args.duration)


if __name__ == "__main__":
    raise SystemExit(main())
