"""SQLite storage (SQLAlchemy): live monitoring records and alerts.

Recorded experiment runs are *not* copied into the database; they are read from the Parquet files and
replayed. Only things that happen while the backend runs are stored: ingested live samples and the
alerts raised for live samples or for a replayed run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MonitoringRecord(Base):
    """One ingested live sample with the predictions made for it."""

    __tablename__ = "monitoring_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(16), index=True)  # always "live"
    stream_id: Mapped[str] = mapped_column(String(64), index=True)
    second: Mapped[int] = mapped_column(Integer)
    timestamp_s: Mapped[float] = mapped_column(Float)
    metrics: Mapped[Dict[str, Any]] = mapped_column(JSON)
    predictions: Mapped[Dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(16), index=True)  # "live" | "replay"
    stream_id: Mapped[str] = mapped_column(String(80), index=True)  # stream name or experiment_id
    second: Mapped[int] = mapped_column(Integer)
    timestamp_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    kind: Mapped[str] = mapped_column(String(24))  # "anomaly" | "congestion"
    severity: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(120))
    explanation: Mapped[str] = mapped_column(Text)  # JSON list of reasons
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "source": self.source, "historical": self.source == "replay", "stream_id": self.stream_id,
            "second": self.second, "timestamp_s": self.timestamp_s, "kind": self.kind, "severity": self.severity,
            "title": self.title, "explanation": json.loads(self.explanation),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Database:
    def __init__(self, path: Path) -> None:
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> Session:
        return self.session_factory()

    # --------------------------------------------------------------------- alerts
    def replace_replay_alerts(self, experiment_id: str, alerts: List[Dict[str, Any]]) -> None:
        """Store the alerts of a replayed run (idempotent: earlier alerts of that run are replaced)."""
        with self.session() as s:
            for old in s.scalars(select(Alert).where(Alert.source == "replay", Alert.stream_id == experiment_id)):
                s.delete(old)
            for a in alerts:
                s.add(Alert(source="replay", stream_id=experiment_id, second=a["second"], timestamp_s=a.get("timestamp_s"),
                            kind=a["kind"], severity=a["severity"], title=a["title"],
                            explanation=json.dumps(a["explanation"])))
            s.commit()

    def add_live_alert(self, stream_id: str, a: Dict[str, Any]) -> Dict[str, Any]:
        with self.session() as s:
            row = Alert(source="live", stream_id=stream_id, second=a["second"], timestamp_s=a.get("timestamp_s"),
                        kind=a["kind"], severity=a["severity"], title=a["title"],
                        explanation=json.dumps(a["explanation"]))
            s.add(row)
            s.commit()
            return row.to_dict()

    def alerts(self, source: Optional[str] = None, stream_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self.session() as s:
            q = select(Alert).order_by(Alert.id.desc()).limit(limit)
            if source:
                q = q.where(Alert.source == source)
            if stream_id:
                q = q.where(Alert.stream_id == stream_id)
            return [a.to_dict() for a in s.scalars(q)]

    # ------------------------------------------------------------- live records
    def add_live_record(self, stream_id: str, second: int, timestamp_s: float, metrics: Dict[str, Any],
                        predictions: Dict[str, Any]) -> None:
        with self.session() as s:
            s.add(MonitoringRecord(source="live", stream_id=stream_id, second=second, timestamp_s=timestamp_s,
                                   metrics=metrics, predictions=predictions))
            s.commit()

    def live_streams(self) -> List[Dict[str, Any]]:
        """Stored live sessions (one per stream id): how many seconds, when they started and ended."""
        from sqlalchemy import func

        with self.session() as s:
            rows = s.execute(select(MonitoringRecord.stream_id, func.count(), func.min(MonitoringRecord.timestamp_s),
                                    func.max(MonitoringRecord.timestamp_s)).group_by(MonitoringRecord.stream_id)).all()
        return sorted(({"stream_id": r[0], "seconds": r[1], "first_timestamp_s": r[2], "last_timestamp_s": r[3],
                        "source": "live"} for r in rows), key=lambda d: d["last_timestamp_s"] or 0, reverse=True)

    def live_records(self, stream_id: Optional[str] = None, limit: int = 300) -> List[Dict[str, Any]]:
        with self.session() as s:
            q = select(MonitoringRecord).order_by(MonitoringRecord.id.desc()).limit(limit)
            if stream_id:
                q = q.where(MonitoringRecord.stream_id == stream_id)
            return [{"source": "live", "historical": False, "stream_id": r.stream_id, "second": r.second,
                     "timestamp_s": r.timestamp_s, "metrics": r.metrics, "predictions": r.predictions}
                    for r in reversed(list(s.scalars(q)))]
