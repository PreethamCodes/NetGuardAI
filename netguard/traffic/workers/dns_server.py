"""Tiny authoritative-style DNS responder (standard library only).

Answers every ``A`` query with a fixed address from the TEST-NET-1
documentation range (192.0.2.0/24); other query types get an empty answer.

    python -m netguard.traffic.workers.dns_server --bind 10.0.0.5 --port 53
"""

from __future__ import annotations

import argparse
import socket
import struct
from typing import Optional, Sequence

DEFAULT_ANSWER_IP = "192.0.2.1"


def build_response(query: bytes, answer_ip: str = DEFAULT_ANSWER_IP) -> Optional[bytes]:
    """Build the DNS response for ``query``; ``None`` if it is not a valid query."""
    if len(query) < 12:
        return None
    tid = query[:2]
    flags, qdcount = struct.unpack("!HH", query[2:6])
    if flags & 0x8000 or qdcount != 1:  # a response, or not exactly one question
        return None
    i = 12
    while True:  # walk the QNAME labels
        if i >= len(query):
            return None
        length = query[i]
        if length == 0:
            i += 1
            break
        if length & 0xC0:  # compression pointers are not valid in a question
            return None
        i += length + 1
    if i + 4 > len(query):
        return None
    qtype, _qclass = struct.unpack("!HH", query[i:i + 4])
    question = query[12:i + 4]
    rd = flags & 0x0100
    answers = b""
    ancount = 0
    if qtype == 1:  # A
        answers = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(answer_ip)
        ancount = 1
    header = tid + struct.pack("!HHHHH", 0x8400 | rd | 0x0080, 1, ancount, 0, 0)
    return header + question + answers


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--bind", required=True, help="IPv4 address of the Mininet host")
    p.add_argument("--port", type=int, default=53)
    p.add_argument("--answer-ip", default=DEFAULT_ANSWER_IP)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))
    try:
        while True:
            data, addr = sock.recvfrom(2048)
            response = build_response(data, args.answer_ip)
            if response is not None:
                sock.sendto(response, addr)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
