"""Paced DNS client. Queries are crafted and replies parsed with Scapy; they are
sent over ordinary UDP sockets, so no raw-socket privileges are needed.

Prints a JSON summary on exit.
"""

from __future__ import annotations

import argparse
import random
import socket
from typing import List, Optional, Sequence


def build_query(domain: str, txid: int) -> bytes:
    """Return the wire format of an ``A`` query for ``domain`` (via Scapy)."""
    from scapy.layers.dns import DNS, DNSQR

    return bytes(DNS(id=txid, rd=1, qd=DNSQR(qname=domain, qtype="A")))


def parse_reply(data: bytes) -> "tuple[int, int]":
    """Return ``(transaction id, answer count)`` of a DNS reply (via Scapy)."""
    from scapy.layers.dns import DNS

    reply = DNS(data)
    if not reply.qr:
        raise ValueError("not a DNS response")
    return int(reply.id), int(reply.ancount)


def query_once(server: str, port: int, domain: str, timeout: float, rng: random.Random) -> int:
    """Send one query and wait for the matching reply; returns the reply size."""
    txid = rng.randrange(0, 65536)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(build_query(domain, txid), (server, port))
        data, _ = sock.recvfrom(4096)
    got_id, answers = parse_reply(data)
    if got_id != txid or answers < 1:
        raise RuntimeError("mismatched or empty DNS reply")
    return len(data)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--server", required=True, help="IPv4 address of a Mininet host")
    p.add_argument("--port", type=int, default=53)
    p.add_argument("--domains", required=True, help="comma separated names to query")
    p.add_argument("--rate", type=float, required=True, help="aggregate queries per second")
    p.add_argument("--duration", type=float, required=True, help="seconds")
    p.add_argument("--clients", type=int, default=1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--timeout", type=float, default=1.0)
    args = p.parse_args(argv)
    args.domain_list = [d.strip() for d in args.domains.split(",") if d.strip()]
    if not args.domain_list:
        p.error("--domains must contain at least one name")
    if args.rate <= 0 or args.duration <= 0 or args.clients < 1:
        p.error("--rate and --duration must be > 0 and --clients >= 1")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    from netguard.traffic.workers.common import emit_summary, run_paced_clients

    args = parse_args(argv)
    domains: List[str] = args.domain_list

    def action(_idx: int, rng: random.Random) -> int:
        return query_once(args.server, args.port, rng.choice(domains), args.timeout, rng)

    stats = run_paced_clients(action, rate=args.rate, clients=args.clients, duration=args.duration, seed=args.seed)
    return emit_summary(stats, args.duration)


if __name__ == "__main__":
    raise SystemExit(main())
