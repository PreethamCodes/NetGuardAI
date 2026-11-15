"""Leakage-free feature engineering for one experiment run (pure functions).

Rules enforced here
-------------------
* **Past/current only.** Every window is *trailing* (seconds ``t-w+1 .. t``) and every lag is a
  positive shift. Nothing looks at ``t+1`` or later.
* **One run at a time.** Functions take the table of a *single* experiment; nothing is computed
  across experiment boundaries, and the first seconds of a run (incomplete windows / lags) are
  ``NaN`` - never interpolated or back-filled.
* **No labels as features.** ``current_congestion`` needs ``t+1, t+2`` (3 s persistence) and
  ``future_congestion`` / ``time_to_congestion`` need the future by definition, so none of the
  label columns is a feature. A causal stand-in (``congested_causal``) is provided instead.

Feature names
-------------
``<signal>``            value at second t (the "1 s window")
``<signal>_mean5`` ...  trailing 5 s mean / max / std / slope (per second)
``<signal>_lag5/10/30`` value 5 / 10 / 30 s earlier
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

WINDOW_S = 5
LAGS_S = (5, 10, 30)

#: base signals that get 1 s + 5 s window features
BASE_SIGNALS = (
    "throughput_mbps", "fwd_pps", "utilisation_pct", "rtt_mean_ms", "jitter_ms", "probe_loss_pct", "loss_pct",
    "queue_backlog_bytes", "queue_delay_ms", "queue_drops_per_s", "active_connections", "tcp_retransmissions",
    "nf_active_flows", "nf_new_flows", "syn_ratio",
)
#: signals that additionally get lag features (the ones that matter for predicting the next 30 s)
LAG_SIGNALS = (
    "throughput_mbps", "utilisation_pct", "queue_delay_ms", "rtt_mean_ms", "loss_pct", "fwd_pps",
    "active_connections", "tcp_retransmissions",
)
PROTOCOL_SHARES = ("tcp", "udp", "icmp", "dns", "http")

#: columns that must never be used as model inputs
TARGET_COLUMNS = (
    "scenario_label", "current_congestion", "future_congestion", "time_to_congestion",
    "future_label_available", "congestion_condition", "labels_status",
)
ID_COLUMNS = ("experiment_id", "scenario", "repetition", "seed", "second", "timestamp_s", "experiment_status")


def _slope(values: np.ndarray) -> float:
    """Least-squares slope per second of a window (NaN if the window has a gap)."""
    if np.isnan(values).any():
        return math.nan
    x = np.arange(len(values), dtype="float64")
    x -= x.mean()
    return float((x * (values - values.mean())).sum() / (x * x).sum())


def worse_direction(frame: pd.DataFrame, suffix: str) -> pd.Series:
    """Max of ``fwd_<suffix>`` and ``rev_<suffix>`` (NaN if either is missing, never filled)."""
    return frame[[f"fwd_{suffix}", f"rev_{suffix}"]].max(axis=1, skipna=False)


def syn_per_second(flows: Optional[pd.DataFrame], n_seconds: int, anchor_wall: float) -> pd.Series:
    """TCP SYN packets of the flows that *started* in each second (NFStream ``bidirectional_syn_packets``).

    SYNs are sent when a flow starts, so attributing a flow's SYN count to its first second is a good
    approximation; flows are only counted at their start second, never earlier.
    """
    out = np.zeros(n_seconds, dtype="float64")
    if flows is None or flows.empty:
        return pd.Series(out)
    tcp = flows[flows["protocol"] == 6]
    start = np.floor(tcp["bidirectional_first_seen_ms"].astype("float64") / 1000.0 - anchor_wall).astype(int)
    for k, syn in zip(start, tcp["bidirectional_syn_packets"].astype("float64")):
        if 0 <= k < n_seconds:
            out[k] += syn
    return pd.Series(out)


def add_base_signals(obs: pd.DataFrame, syn: Optional[pd.Series] = None) -> pd.DataFrame:
    """Derived per-second signals: queue summary, protocol shares, SYN ratio. Input rows must be one run."""
    out = obs.copy().reset_index(drop=True)
    out["queue_backlog_bytes"] = worse_direction(out, "queue_backlog_bytes")
    out["queue_drops_per_s"] = worse_direction(out, "queue_drops_per_s")
    pkts = out["tshark_pkts"].astype("float64")
    for proto in PROTOCOL_SHARES:
        share = out[f"tshark_{proto}_pkts"].astype("float64") / pkts
        out[f"share_{proto}"] = share.where(pkts > 0, 0.0)  # no packets in that second: no traffic of any kind
    tcp = out["tshark_tcp_pkts"].astype("float64")
    if syn is None:
        out["syn_ratio"] = math.nan  # unknown, not zero: the flow table was not available
    else:
        out["syn_ratio"] = (syn.reset_index(drop=True) / tcp).where(tcp > 0, 0.0)
    return out


def trailing_features(frame: pd.DataFrame, signals: Sequence[str], window: int = WINDOW_S) -> Dict[str, pd.Series]:
    """mean / max / std / slope over the last ``window`` seconds, only where the whole window exists."""
    cols: Dict[str, pd.Series] = {}
    for sig in signals:
        roll = frame[sig].rolling(window=window, min_periods=window)  # trailing; includes second t
        cols[f"{sig}_mean{window}"] = roll.mean()
        cols[f"{sig}_max{window}"] = roll.max()
        cols[f"{sig}_std{window}"] = roll.std(ddof=0)
        cols[f"{sig}_slope{window}"] = roll.apply(_slope, raw=True)
    return cols


def lag_features(frame: pd.DataFrame, signals: Sequence[str], lags: Sequence[int] = LAGS_S) -> Dict[str, pd.Series]:
    cols: Dict[str, pd.Series] = {}
    for sig in signals:
        for lag in lags:
            cols[f"{sig}_lag{lag}"] = frame[sig].shift(lag)  # positive shift = the past
    return cols


def causal_congestion(
    frame: pd.DataFrame, util_pct: float, queue_ms: float, loss_pct: float, min_consecutive: int,
) -> pd.Series:
    """Causal "currently congested" state: the congestion condition held for the last ``min_consecutive`` seconds.

    Unlike the ground-truth ``current_congestion`` (which also marks the first seconds of an episode and so
    needs the future) this only uses seconds ``<= t``. Unmeasured seconds give NaN, never True.
    """
    util = frame["utilisation_pct"]
    cond = (util >= util_pct) & ((frame["queue_delay_ms"] >= queue_ms) | (frame["loss_pct"] > loss_pct))
    known = util.notna() & frame["queue_delay_ms"].notna() & frame["loss_pct"].notna()
    run = cond.astype("float64").where(known).rolling(min_consecutive, min_periods=min_consecutive).min()
    return run


def build_features(
    obs: pd.DataFrame, *, flows: Optional[pd.DataFrame], anchor_wall: float,
    util_pct: float, queue_ms: float, loss_pct: float, min_consecutive: int, syn: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """Feature table (+ IDs and label columns) for one run, one row per second, ordered by second.

    ``syn`` (SYN packets of flows started per second) can be given directly, e.g. by a live monitor that has no
    flow table; otherwise it is derived from ``flows``.
    """
    if obs["experiment_id"].nunique() != 1:
        raise ValueError("build_features works on exactly one experiment")
    obs = obs.sort_values("second").reset_index(drop=True)
    if syn is None and flows is not None:
        syn = syn_per_second(flows, len(obs), anchor_wall)
    base = add_base_signals(obs, syn)
    cols: Dict[str, pd.Series] = {}
    cols.update(trailing_features(base, BASE_SIGNALS))
    cols.update(lag_features(base, LAG_SIGNALS))
    cols["congested_causal"] = causal_congestion(base, util_pct, queue_ms, loss_pct, min_consecutive)
    # VM CPU / RAM are kept in the dataset but are not model inputs: they describe the emulation host, not the network
    feature_cols = list(BASE_SIGNALS) + [f"share_{p}" for p in PROTOCOL_SHARES]
    out = pd.concat(
        [obs[[c for c in ID_COLUMNS if c in obs.columns]],
         base[feature_cols],
         pd.DataFrame(cols),
         obs[[c for c in TARGET_COLUMNS if c in obs.columns]]],
        axis=1,
    )
    return out


def feature_columns(frame: pd.DataFrame) -> List[str]:
    """Model input columns: everything except IDs and labels."""
    excluded = set(ID_COLUMNS) | set(TARGET_COLUMNS)
    return [c for c in frame.columns if c not in excluded]
