"""Command line interface (commands that work on any OS)."""

from __future__ import annotations

import json

import pytest

from netguard.cli import main


def test_validate(capsys):
    assert main(["validate"]) == 0
    out = capsys.readouterr().out
    assert "environment OK" in out and "validated" in out


def test_list(capsys):
    assert main(["list"]) == 0
    assert "ramp" in capsys.readouterr().out


def test_plan_summary(capsys):
    assert main(["plan", "udp_flood", "--events"]) == 0
    out = capsys.readouterr().out
    assert "UDP_FLOOD" in out and "h1->h5" in out


def test_plan_json_is_reproducible(capsys):
    main(["plan", "random", "--json"])
    first = json.loads(capsys.readouterr().out)
    main(["plan", "random", "--json"])
    assert json.loads(capsys.readouterr().out) == first
    main(["plan", "random", "--json", "--seed", "999"])
    assert json.loads(capsys.readouterr().out) != first


def test_plan_overrides(capsys):
    main(["plan", "normal", "--duration", "60", "--seed", "5", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["duration_s"] == 60 and data["seed"] == 5


@pytest.mark.parametrize("argv", [["plan", "no-such-scenario"], ["--config-dir", "/nonexistent", "validate"]])
def test_errors_return_exit_code_2(argv):
    assert main(argv) == 2


def test_no_command_is_a_usage_error():
    with pytest.raises(SystemExit):
        main([])


def test_link_overrides_reach_the_plan(capsys):
    main(["plan", "ramp", "--json"])
    base = json.loads(capsys.readouterr().out)
    main(["plan", "ramp", "--json", "--bandwidth-mbps", "20"])
    fast = json.loads(capsys.readouterr().out)
    top = lambda d: max(e["rate_mbps"] for e in d["events"])  # noqa: E731
    assert top(fast) == pytest.approx(2 * top(base), rel=0.01)  # ramp load scales with capacity


def test_invalid_link_override_is_reported(capsys):
    assert main(["plan", "normal", "--loss-pct", "250"]) == 2
    assert main(["plan", "normal", "--bandwidth-mbps", "500"]) == 2  # would exceed the 100 Mbit/s access links
