"""Python worker programs, exercised over loopback only (127.0.0.1)."""

from __future__ import annotations

import json
import socket
import struct
import threading

import pytest

from netguard.traffic.workers import dns_server, http_client, http_server
from netguard.traffic.workers.common import ClientStats, paced_interval, run_paced_clients


# ------------------------------------------------------------------------- DNS
def raw_query(name: str = "www.test", qtype: int = 1, txid: int = 0x1234, flags: int = 0x0100) -> bytes:
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return struct.pack("!HHHHHH", txid, flags, 1, 0, 0, 0) + qname + struct.pack("!HH", qtype, 1)


class TestDnsServer:
    def test_a_query_gets_one_answer(self):
        response = dns_server.build_response(raw_query())
        txid, flags, qd, an, _, _ = struct.unpack("!HHHHHH", response[:12])
        assert txid == 0x1234 and qd == 1 and an == 1
        assert flags & 0x8000 and flags & 0x0100 and flags & 0x0400  # QR, RD echoed, AA
        assert response[-4:] == socket.inet_aton(dns_server.DEFAULT_ANSWER_IP)

    def test_other_types_get_empty_answer(self):
        response = dns_server.build_response(raw_query(qtype=28))
        assert struct.unpack("!H", response[6:8])[0] == 0

    def test_custom_answer_ip(self):
        assert dns_server.build_response(raw_query(), "192.0.2.77")[-4:] == socket.inet_aton("192.0.2.77")

    @pytest.mark.parametrize("data", [
        b"", b"\x00" * 11,
        raw_query(flags=0x8100),  # already a response
        raw_query()[:-3],  # truncated question
        raw_query()[:12] + b"\xc0\x0c" + b"\x00\x01\x00\x01",  # compression pointer in question
        struct.pack("!HHHHHH", 1, 0x0100, 0, 0, 0, 0),  # no question
    ])
    def test_invalid_queries_are_ignored(self, data):
        assert dns_server.build_response(data) is None


class TestDnsClient:
    @pytest.fixture(autouse=True)
    def _need_scapy(self):
        pytest.importorskip("scapy", reason="scapy is needed for the DNS client")

    def test_scapy_query_is_understood_by_server(self):
        from netguard.traffic.workers import dns_client

        query = dns_client.build_query("mail.example.test", 4321)
        response = dns_server.build_response(query)
        assert response is not None
        txid, answers = dns_client.parse_reply(response)
        assert (txid, answers) == (4321, 1)

    def test_query_once_over_loopback(self):
        import random

        from netguard.traffic.workers import dns_client

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(3)

        def serve() -> None:
            data, addr = sock.recvfrom(2048)
            sock.sendto(dns_server.build_response(data), addr)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            size = dns_client.query_once("127.0.0.1", sock.getsockname()[1], "example.test", 2.0, random.Random(1))
            assert size > 12
        finally:
            thread.join(3)
            sock.close()

    def test_timeout_is_an_error(self):
        import random

        from netguard.traffic.workers import dns_client

        silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        silent.bind(("127.0.0.1", 0))
        try:
            with pytest.raises(socket.timeout):
                dns_client.query_once("127.0.0.1", silent.getsockname()[1], "x.test", 0.2, random.Random(1))
        finally:
            silent.close()

    def test_argument_validation(self):
        from netguard.traffic.workers import dns_client

        with pytest.raises(SystemExit):
            dns_client.parse_args(["--server", "10.0.0.1", "--domains", " , ", "--rate", "1", "--duration", "1"])
        with pytest.raises(SystemExit):
            dns_client.parse_args(["--server", "10.0.0.1", "--domains", "a.test", "--rate", "0", "--duration", "1"])


# ------------------------------------------------------------------------ HTTP
@pytest.fixture
def web_server():
    server = http_server._Server(("127.0.0.1", 0), http_server.make_handler(b"x" * 2048))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class TestHttp:
    def test_fetch_payload(self, web_server):
        assert http_client.fetch(web_server + "/payload", 2.0) == 2048

    def test_non_200_is_an_error(self, web_server):
        with pytest.raises(RuntimeError, match="404"):
            http_client.fetch(web_server + "/missing", 2.0)

    def test_only_plain_http_urls(self):
        with pytest.raises(ValueError):
            http_client.fetch("https://127.0.0.1/payload", 1.0)

    def test_client_main_reports_json_summary(self, web_server, capsys):
        code = http_client.main(["--url", web_server + "/payload", "--rate", "20", "--duration", "1.5",
                                 "--clients", "2", "--seed", "3"])
        assert code == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["errors"] == 0
        assert 15 <= summary["attempts"] <= 45  # ~20 req/s for 1.5 s, jittered
        assert summary["bytes"] == summary["attempts"] * 2048

    def test_all_failures_give_nonzero_exit(self, capsys):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()  # nothing listens here now
        code = http_client.main(["--url", f"http://127.0.0.1:{port}/payload", "--rate", "10", "--duration", "1",
                                 "--timeout", "0.5"])
        summary = json.loads(capsys.readouterr().out)
        assert code == 1 and summary["attempts"] > 0 and summary["errors"] == summary["attempts"]

    @pytest.mark.parametrize("args", [["--rate", "0"], ["--duration", "0"], ["--clients", "0"]])
    def test_client_argument_validation(self, args):
        base = ["--url", "http://127.0.0.1:1/payload", "--rate", "1", "--duration", "1"]
        merged = dict(zip(base[::2], base[1::2]))
        merged.update(dict(zip(args[::2], args[1::2])))
        argv = [x for kv in merged.items() for x in kv]
        with pytest.raises(SystemExit):
            http_client.parse_args(argv)


class TestPacing:
    def test_interval(self):
        assert paced_interval(10, 2) == pytest.approx(0.2)
        assert paced_interval(0.5, 1) == 2

    @pytest.mark.parametrize("rate,clients", [(0, 1), (-1, 1), (1, 0)])
    def test_invalid(self, rate, clients):
        with pytest.raises(ValueError):
            paced_interval(rate, clients)

    def test_failures_are_counted_not_hidden(self):
        def action(idx, rng):
            raise OSError("boom")

        stats = run_paced_clients(action, rate=20, clients=1, duration=0.6, seed=1)
        assert stats.attempts > 0 and stats.errors == stats.attempts
        assert stats.summary(0.6)["mean_latency_ms"] is None

    def test_same_seed_same_schedule_shape(self):
        stats = ClientStats(attempts=4, errors=1, bytes=300, latency_sum_s=0.3)
        summary = stats.summary(2)
        assert summary["successes"] == 3 and summary["mean_latency_ms"] == pytest.approx(100.0)
