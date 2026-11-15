"""FastAPI application: REST + WebSockets for the NetGuard AI dashboard.

    python -m netguard serve                  (or: uvicorn netguard.backend.app:create_app --factory)

Data sources are always labelled:

* ``source = "replay"`` / ``historical = true`` - a recorded experiment run read from Parquet.
* ``source = "live"``  / ``historical = false`` - a sample posted to ``POST /api/ingest`` while the
  backend runs. Nothing is invented: with no live monitor connected there are simply no live records.

The system only recommends; it never changes any network configuration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from netguard.backend.db import Database
from netguard.backend.inference import ModelChoice, Predictor
from netguard.backend.live import IngestSample, LiveStreams
from netguard.backend.replay import ReplayStore
from netguard.config.loader import DEFAULT_CONFIG_DIR
from netguard.experiments.calibration import effective_labeling
from netguard.experiments.config import LabelingConfig, load_labeling

logger = logging.getLogger(__name__)

PROJECT_ROOT = DEFAULT_CONFIG_DIR.parent

RECOMMENDATIONS = {
    "congestion": "Congestion is likely soon: consider shaping or rate-limiting the heaviest flows. NetGuard AI only "
                  "recommends and never changes the network itself.",
    "flood-like": "Sustained high-rate stream with few flows: inspect the source hosts; consider rate-limiting that traffic.",
    "legitimate burst": "Many clients arrived at once with no sustained loss: likely a legitimate spike; capacity "
                        "planning may help.",
    "degradation": "Latency/loss is high while the link is not loaded: check the path (delay or loss on the link).",
    "unclassified anomaly": "Unusual traffic without a matching rule: review the metrics manually.",
}


class Hub:
    """Connected WebSocket clients of the live feed."""

    def __init__(self) -> None:
        self.clients: Set[WebSocket] = set()

    async def broadcast(self, message: Dict[str, Any]) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 - a dead client must not stop the others
                self.clients.discard(ws)


def create_app(data_dir: Optional[Path] = None, models_dir: Optional[Path] = None, results_dir: Optional[Path] = None,
               db_path: Optional[Path] = None, config_dir: Path = DEFAULT_CONFIG_DIR,
               choice: Optional[ModelChoice] = None, labeling: Optional[LabelingConfig] = None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("NETGUARD_DATA_DIR", PROJECT_ROOT / "data"))
    models_dir = Path(models_dir or os.environ.get("NETGUARD_MODELS_DIR", PROJECT_ROOT / "models"))
    results_dir = Path(results_dir or os.environ.get("NETGUARD_RESULTS_DIR", PROJECT_ROOT / "results"))
    db_path = Path(db_path or os.environ.get("NETGUARD_DB", data_dir / "backend" / "netguard.db"))

    predictor = Predictor(models_dir, results_dir, choice)
    labeling = labeling or effective_labeling(load_labeling(config_dir), data_dir)
    store = ReplayStore(data_dir, predictor)
    db = Database(db_path)
    live = LiveStreams(predictor, labeling)
    hub = Hub()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(title="NetGuard AI", version="0.1.0", lifespan=lifespan,
                  description="Network traffic analysis, anomaly detection and 30 s congestion prediction.")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])
    app.state.predictor, app.state.store, app.state.db, app.state.live = predictor, store, db, live

    # ------------------------------------------------------------------- meta
    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {"status": "ok", "models": predictor.describe(), "replay_runs": len(store.runs()),
                "live_streams": len(live._counter)}

    @app.get("/api/models")
    def models() -> Dict[str, Any]:
        """Model performance exactly as measured on the held-out runs (results/ml_results.json)."""
        path = results_dir / "ml_results.json"
        return {"in_use": predictor.describe(), "evaluation": json.loads(path.read_text()) if path.is_file() else None,
                "note": "Metrics come from held-out experiment runs; see results/ml_results.md for the caveats."}

    @app.get("/api/data-summary")
    def data_summary() -> Dict[str, Any]:
        path = results_dir / "prep_report.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    # ---------------------------------------------------------------- replay
    @app.get("/api/runs")
    def runs() -> List[Dict[str, Any]]:
        return store.runs()

    @app.get("/api/runs/{experiment_id}/timeline")
    def timeline(experiment_id: str) -> Dict[str, Any]:
        try:
            tl = store.timeline(experiment_id)
        except KeyError:
            raise HTTPException(404, f"unknown run {experiment_id!r}") from None
        db.replace_replay_alerts(experiment_id, tl["alerts"])
        return tl

    @app.get("/api/runs/{experiment_id}/summary")
    def run_summary(experiment_id: str) -> Dict[str, Any]:
        try:
            tl = store.timeline(experiment_id)
        except KeyError:
            raise HTTPException(404, f"unknown run {experiment_id!r}") from None
        recs = [r for r in tl["records"] if r["predictions"].get("available")]
        flagged = sum(r["predictions"]["anomaly"]["flagged"] for r in recs)
        warned = sum(r["predictions"]["congestion"]["warning"] for r in recs)
        return {"experiment_id": experiment_id, "scenario": tl["scenario"], "split": tl["split"], "historical": True,
                "seen_by_models": tl["seen_by_models"], "seconds": len(tl["records"]), "predicted_seconds": len(recs),
                "anomaly_seconds": int(flagged), "congestion_warning_seconds": int(warned),
                "alerts": len(tl["alerts"])}

    @app.websocket("/ws/replay/{experiment_id}")
    async def ws_replay(ws: WebSocket, experiment_id: str, speed: float = Query(10.0, gt=0, le=200)) -> None:
        """Stream a recorded run second by second (``speed`` x real time). Every message is marked historical."""
        await ws.accept()
        try:
            tl = store.timeline(experiment_id)
        except KeyError:
            await ws.send_json({"error": f"unknown run {experiment_id!r}"})
            await ws.close(code=4404)
            return
        try:
            await ws.send_json({"type": "start", "experiment_id": experiment_id, "source": "replay", "historical": True,
                                "seconds": len(tl["records"]), "speed": speed, "seen_by_models": tl["seen_by_models"]})
            for rec in tl["records"]:
                await ws.send_json({"type": "record", **rec})
                await asyncio.sleep(1.0 / speed)
            await ws.send_json({"type": "end", "experiment_id": experiment_id})
            await ws.close()
        except WebSocketDisconnect:
            return

    # ------------------------------------------------------------------ live
    @app.post("/api/ingest")
    async def ingest(sample: IngestSample) -> Dict[str, Any]:
        """Post one second of measurements from a live monitor; returns the predictions made for it."""
        try:
            record = live.ingest(sample)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        db.add_live_record(sample.stream_id, record["second"], sample.timestamp_s, record["metrics"], record["predictions"])
        stored = [db.add_live_alert(sample.stream_id, a) for a in record["alerts"]]
        record["alerts"] = stored
        await hub.broadcast({"type": "record", **record})
        return record

    @app.get("/api/live/records")
    def live_records(stream_id: Optional[str] = None, limit: int = Query(300, le=2000)) -> Dict[str, Any]:
        recs = db.live_records(stream_id, limit)
        return {"source": "live", "historical": False, "count": len(recs), "records": recs,
                "note": "" if recs else "No live samples received: connect a monitor to POST /api/ingest."}

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        await ws.accept()
        hub.clients.add(ws)
        try:
            await ws.send_json({"type": "hello", "source": "live", "historical": False})
            while True:
                await ws.receive_text()  # keep the connection open; clients send nothing
        except WebSocketDisconnect:
            hub.clients.discard(ws)

    @app.delete("/api/live/{stream_id}")
    def reset_stream(stream_id: str) -> Dict[str, str]:
        live.reset(stream_id)
        return {"status": "reset", "stream_id": stream_id}

    # ---------------------------------------------------------------- alerts
    @app.get("/api/alerts")
    def alerts(source: Optional[str] = Query(None, pattern="^(live|replay)$"), stream_id: Optional[str] = None,
               limit: int = Query(100, le=1000)) -> List[Dict[str, Any]]:
        return db.alerts(source, stream_id, limit)

    @app.get("/api/recommendations")
    def recommendations() -> Dict[str, str]:
        return RECOMMENDATIONS

    return app
