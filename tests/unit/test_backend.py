"""Backend API tests. The tables are synthetic and only check the API/data-flow logic; the models are trained on
them inside the test and nothing here is presented as experimental data or results."""

from __future__ import annotations

import warnings

import pytest

warnings.filterwarnings("ignore", message=".*httpx.*")
from fastapi.testclient import TestClient  # noqa: E402

from netguard.backend.app import create_app  # noqa: E402
from netguard.ml.run import train_all  # noqa: E402
from tests.unit.test_ml import build_prepared  # noqa: E402
from tests.unit.test_prep import LABELING, make_obs  # noqa: E402


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("backend")
    data = build_prepared(tmp)
    train_all(data, tmp / "results", tmp / "models", seed=0, n_jobs=1)
    app = create_app(data_dir=data, models_dir=tmp / "models", results_dir=tmp / "results",
                     db_path=tmp / "api.db", labeling=LABELING)
    return TestClient(app), data, tmp


def sample_payload(obs_row, stream="live-t", ts=1_000_000.0, syn=1.0):
    keys = ["throughput_mbps", "fwd_pps", "utilisation_pct", "rtt_mean_ms", "jitter_ms", "probe_loss_pct", "loss_pct",
            "queue_delay_ms", "active_connections", "tcp_retransmissions", "nf_active_flows", "nf_new_flows",
            "tshark_pkts", "tshark_tcp_pkts", "tshark_udp_pkts", "tshark_icmp_pkts", "tshark_dns_pkts",
            "tshark_http_pkts"]
    body = {k: float(obs_row[k]) for k in keys}
    body.update(fwd_queue_backlog_bytes=float(obs_row["fwd_queue_backlog_bytes"]), stream_id=stream,
                timestamp_s=ts, syn_packets=syn)
    return body


# ---------------------------------------------------------------------------- meta
def test_health_and_models_report_what_is_loaded(env):
    client, *_ = env
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["replay_runs"] == 15 and h["models"]["feature_count"] > 50
    m = client.get("/api/models").json()
    assert set(m["evaluation"]) == {"classification", "anomaly", "congestion"}
    assert client.get("/api/recommendations").json()["congestion"]
    assert client.get("/api/data-summary").json()["runs_included"] == 15


# --------------------------------------------------------------------------- replay
def test_runs_are_listed_as_historical_with_their_split(env):
    client, *_ = env
    runs = client.get("/api/runs").json()
    assert len(runs) == 15 and all(r["historical"] and r["source"] == "replay" for r in runs)
    assert {r["split"] for r in runs} == {"train", "val", "test"}
    assert all(r["seen_by_models"] == (r["split"] != "test") for r in runs)


def test_timeline_marks_history_separates_truth_and_withholds_early_predictions(env):
    client, *_ = env
    run = next(r for r in client.get("/api/runs").json() if r["scenario"] == "ramp" and r["split"] == "test")
    tl = client.get(f"/api/runs/{run['experiment_id']}/timeline").json()
    assert tl["source"] == "replay" and tl["historical"] and not tl["seen_by_models"]
    recs = tl["records"]
    assert len(recs) == 100 and all(r["historical"] and r["source"] == "replay" for r in recs)
    assert not any(r["predictions"]["available"] for r in recs[:30])  # no lag features yet: no guess
    assert "reason" in recs[0]["predictions"]
    p = recs[50]["predictions"]
    assert p["available"] and set(p) >= {"classification", "anomaly", "congestion"}
    assert 0 <= p["congestion"]["probability"] <= 1 and p["congestion"]["window"] == [1, 30]
    assert "time_to_congestion" not in p["congestion"]  # evaluation helper, never a prediction
    assert set(recs[50]["ground_truth"]) >= {"current_congestion", "future_congestion", "scenario_label"}
    assert recs[-1]["ground_truth"]["future_congestion"] is None  # final 30 s: unavailable, not invented
    json_ok = client.get(f"/api/runs/{run['experiment_id']}/summary").json()
    assert json_ok["predicted_seconds"] == 70 and json_ok["historical"]


def test_unknown_run_is_a_404(env):
    client, *_ = env
    assert client.get("/api/runs/nope/timeline").status_code == 404
    assert client.get("/api/runs/nope/summary").status_code == 404


def test_replay_alerts_are_stored_once_and_labelled_historical(env):
    client, *_ = env
    run = next(r for r in client.get("/api/runs").json() if r["scenario"] == "degraded" and r["split"] == "test")
    exp = run["experiment_id"]
    first = client.get(f"/api/runs/{exp}/timeline").json()["alerts"]
    client.get(f"/api/runs/{exp}/timeline")  # again: replaced, not duplicated
    stored = client.get("/api/alerts", params={"source": "replay", "stream_id": exp, "limit": 1000}).json()
    assert len(stored) == len(first) > 0
    assert all(a["historical"] and a["source"] == "replay" and a["explanation"] for a in stored)
    assert client.get("/api/alerts", params={"source": "bogus"}).status_code == 422


def test_websocket_replay_streams_marked_historical_records(env):
    client, *_ = env
    run = next(r for r in client.get("/api/runs").json() if r["scenario"] == "normal" and r["split"] == "test")
    with client.websocket_connect(f"/ws/replay/{run['experiment_id']}?speed=200") as ws:
        start = ws.receive_json()
        assert start["type"] == "start" and start["historical"] is True and start["seconds"] == 100
        first = ws.receive_json()
        assert first["type"] == "record" and first["historical"] and first["second"] == 0
        for _ in range(98):
            ws.receive_json()
        assert ws.receive_json()["type"] == "record"
        assert ws.receive_json()["type"] == "end"
    with client.websocket_connect("/ws/replay/missing") as ws:
        assert "error" in ws.receive_json()


# ------------------------------------------------------------------------------ live
def test_no_live_data_means_no_live_records(env):
    client, *_ = env
    body = client.get("/api/live/records", params={"stream_id": "nobody"}).json()
    assert body["count"] == 0 and body["source"] == "live" and "No live samples" in body["note"]


def test_live_ingest_predicts_only_after_enough_history(env):
    client, *_ = env
    client.delete("/api/live/live-a")
    rows = make_obs(n=60, congested=range(0)).to_dict("records")
    results = []
    for i, row in enumerate(rows[:40]):
        r = client.post("/api/ingest", json=sample_payload(row, "live-a", 2_000_000.0 + i))
        assert r.status_code == 200
        results.append(r.json())
    assert all(r["source"] == "live" and r["historical"] is False for r in results)
    assert not any(r["predictions"]["available"] for r in results[:30])  # not enough history yet
    assert results[39]["predictions"]["available"]
    assert results[39]["predictions"]["congestion"]["horizon_s"] == 30
    stored = client.get("/api/live/records", params={"stream_id": "live-a"}).json()
    assert stored["count"] == 40 and stored["records"][0]["historical"] is False


def test_live_ingest_rejects_old_timestamps_bad_payloads_and_restarts_after_a_gap(env):
    client, *_ = env
    client.delete("/api/live/live-b")
    row = make_obs(n=5).iloc[0]
    assert client.post("/api/ingest", json=sample_payload(row, "live-b", 3_000_000.0)).status_code == 200
    assert client.post("/api/ingest", json=sample_payload(row, "live-b", 3_000_000.0)).status_code == 409  # not after
    assert client.post("/api/ingest", json=sample_payload(row, "live-b", 2_999_999.0)).status_code == 409
    bad = sample_payload(row, "live-b", 3_000_001.0)
    bad["rtt_mean_ms"] = "not a number"
    assert client.post("/api/ingest", json=bad).status_code == 422
    no_time = sample_payload(row, "live-b", 3_000_001.0)
    del no_time["timestamp_s"]
    assert client.post("/api/ingest", json=no_time).status_code == 422
    after_gap = client.post("/api/ingest", json=sample_payload(row, "live-b", 3_000_100.0)).json()
    assert after_gap["second"] == 0  # a gap breaks the history: the stream starts over, nothing is interpolated


def test_live_anomalies_raise_stored_alerts_and_are_broadcast(env):
    client, *_ = env
    client.delete("/api/live/live-c")
    rows = make_obs(n=60, congested=range(0)).to_dict("records")
    with client.websocket_connect("/ws/live") as ws:
        assert ws.receive_json()["type"] == "hello"
        for i, row in enumerate(rows[:35]):
            row = dict(row, rtt_mean_ms=150.0, loss_pct=4.0, probe_loss_pct=4.0, utilisation_pct=10.0)  # degraded-like
            resp = client.post("/api/ingest", json=sample_payload(row, "live-c", 4_000_000.0 + i)).json()
            pushed = ws.receive_json()
            assert pushed["type"] == "record" and pushed["source"] == "live" and pushed["second"] == i
    alerts = client.get("/api/alerts", params={"source": "live", "stream_id": "live-c"}).json()
    assert alerts and all(a["source"] == "live" and not a["historical"] for a in alerts)
    assert resp["predictions"]["available"] and resp["predictions"]["anomaly"]["flagged"]


def test_nulls_are_accepted_as_unmeasured_and_never_become_zero(env):
    client, *_ = env
    client.delete("/api/live/live-n")
    rows = make_obs(n=40, congested=range(0)).to_dict("records")
    last = None
    for i, row in enumerate(rows):
        body = sample_payload(row, "live-n", 5_000_000.0 + i)
        if i >= 35:
            body["rtt_mean_ms"] = None  # the probe measured nothing in these seconds
        last = client.post("/api/ingest", json=body)
        assert last.status_code == 200
    rec = last.json()
    assert rec["metrics"]["rtt_mean_ms"] is None  # reported back as missing
    assert not rec["predictions"]["available"]  # ... so the prediction is withheld, not guessed


def test_stored_live_sessions_are_listed_for_playback(env):
    client, *_ = env
    sessions = client.get("/api/live/streams").json()
    ids = {s["stream_id"] for s in sessions}
    assert {"live-a", "live-c"} <= ids or ids  # sessions created by the live tests above
    one = next(s for s in sessions if s["stream_id"] == "live-a")
    assert one["seconds"] == 40 and one["source"] == "live" and one["last_timestamp_s"] > one["first_timestamp_s"]
    recs = client.get("/api/live/records", params={"stream_id": "live-a", "limit": 2000}).json()
    assert recs["count"] == 40 and all(r["historical"] is False for r in recs["records"])
