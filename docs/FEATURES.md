# NetGuard AI — Features and why each is needed

Generated from the real files `data/processed/*.parquet` and `data/features/feature_columns.json`. Two levels exist: **52 raw per-second measurements** collected from the network, and **105 model input features** engineered from them.

## 1. Where the numbers come from

| Level | Count | Stored in |
|---|---|---|
| Raw per-second measurements | 52 | `data/processed/<run>.parquet` (66 columns with 7 identifiers and 7 labels) |
| Model input features | 105 | `data/features/features.parquet` (81 without the lag features) |

## 2. The 105 model input features

### 2.1 Base signals at the current second (15)

| # | Feature | What it is | Why it is needed |
|---|---|---|---|
| 1 | `throughput_mbps` | Data actually carried on the bottleneck per second (the busier direction). | Shows how loaded the network is. Rises in RAMP, UDP_FLOOD and FLASH_CROWD; stays low in NORMAL and DEGRADED. |
| 2 | `fwd_pps` | Packets per second sent toward the far side. | Separates many small packets (DNS, HTTP, pings) from few large ones (bulk transfers). Floods raise it sharply. |
| 3 | `utilisation_pct` | Throughput divided by the 10 Mbit/s capacity, in %. | The main driver of congestion: the congestion rule needs it at 90 % or more. Also the basis of the '>80 %' baseline. |
| 4 | `rtt_mean_ms` | Mean round-trip time of the 0.2 s ping probes. | Waiting in a full queue adds delay, so RTT climbs before and during congestion; DEGRADED shows high RTT with low load. |
| 5 | `jitter_ms` | Mean change in RTT between consecutive probes. | A queue that is filling and emptying makes delay unstable; helps tell degradation and congestion from calm traffic. |
| 6 | `probe_loss_pct` | Share of ping probes that got no reply. | End-to-end view of loss, as a user would feel it. Coarse (5 probes per second) so it is not used in the label. |
| 7 | `loss_pct` | Packets dropped by the bottleneck (queue overflow plus random loss), % of packets. | A direct sign of congestion (queue overflow) or degradation (random loss); part of the congestion rule (> 1 %). |
| 8 | `queue_backlog_bytes` | Bytes waiting in the bottleneck queue (the fuller direction). | The earliest physical sign of a coming jam: the queue fills before packets are lost. |
| 9 | `queue_delay_ms` | Backlog divided by link rate: how long a packet waits. | Part of the congestion rule (>= X = 29 ms). Converts queue size into user-visible delay. |
| 10 | `queue_drops_per_s` | Packets per second dropped because the queue was full. | Only happens when the queue overflows, so it separates true congestion from random loss. |
| 11 | `active_connections` | Number of open TCP connections across all hosts. | Many connections at once is the signature of a flash crowd; few long connections is typical of bulk transfers. |
| 12 | `tcp_retransmissions` | New TCP segments re-sent per second. | TCP reacts to loss by re-sending, so a rise confirms that loss is hurting real connections. |
| 13 | `nf_active_flows` | Flows alive in that second (NFStream). | Many flows = many clients (flash crowd); few flows with huge volume = flood-like. The classifier's most important feature. |
| 14 | `nf_new_flows` | Flows that started in that second. | Arrival rate of new conversations: spikes in flash crowds and connection storms. |
| 15 | `syn_ratio` | SYN packets divided by TCP packets in the second. | A high share means many connections are being opened (flash crowd or SYN-style flood) rather than data being sent. |

### 2.2 Protocol shares (5)

| # | Feature | Why it is needed |
|---|---|---|
| 16 | `share_tcp` | Fraction of packets that are TCP: web and file traffic dominate flash crowds and normal traffic. |
| 17 | `share_udp` | Fraction that are UDP: floods and RAMP are almost entirely UDP; the single strongest hint of flood-like traffic. |
| 18 | `share_icmp` | Fraction that are ICMP (ping): relevant when probes dominate quiet periods. |
| 19 | `share_dns` | Fraction that are DNS (part of UDP): name-lookup activity, typical of normal use. |
| 20 | `share_http` | Fraction that are web traffic (part of TCP): marks web-like behaviour such as flash crowds. |

### 2.3 Statistics over the last 5 seconds (60 = 15 signals × 4)

Each of the 15 base signals gets four statistics. Only past and current seconds are used, and a value is produced only when all 5 seconds exist.

| Statistic | Meaning | Why it is needed |
|---|---|---|
| `_mean5` | Average of the last 5 seconds | Smooths one-second noise and shows the current level. |
| `_max5` | Highest value in the last 5 seconds | Catches short peaks that an average hides. |
| `_std5` | Standard deviation over the last 5 seconds | Measures burstiness and instability; steady traffic has a low value. |
| `_slope5` | Trend per second over the last 5 seconds | Says whether the signal is rising or falling: the key early-warning cue (a rising utilisation or queue precedes congestion). |

All 60 names:

- `throughput_mbps` → `throughput_mbps_mean5`, `throughput_mbps_max5`, `throughput_mbps_std5`, `throughput_mbps_slope5`
- `fwd_pps` → `fwd_pps_mean5`, `fwd_pps_max5`, `fwd_pps_std5`, `fwd_pps_slope5`
- `utilisation_pct` → `utilisation_pct_mean5`, `utilisation_pct_max5`, `utilisation_pct_std5`, `utilisation_pct_slope5`
- `rtt_mean_ms` → `rtt_mean_ms_mean5`, `rtt_mean_ms_max5`, `rtt_mean_ms_std5`, `rtt_mean_ms_slope5`
- `jitter_ms` → `jitter_ms_mean5`, `jitter_ms_max5`, `jitter_ms_std5`, `jitter_ms_slope5`
- `probe_loss_pct` → `probe_loss_pct_mean5`, `probe_loss_pct_max5`, `probe_loss_pct_std5`, `probe_loss_pct_slope5`
- `loss_pct` → `loss_pct_mean5`, `loss_pct_max5`, `loss_pct_std5`, `loss_pct_slope5`
- `queue_backlog_bytes` → `queue_backlog_bytes_mean5`, `queue_backlog_bytes_max5`, `queue_backlog_bytes_std5`, `queue_backlog_bytes_slope5`
- `queue_delay_ms` → `queue_delay_ms_mean5`, `queue_delay_ms_max5`, `queue_delay_ms_std5`, `queue_delay_ms_slope5`
- `queue_drops_per_s` → `queue_drops_per_s_mean5`, `queue_drops_per_s_max5`, `queue_drops_per_s_std5`, `queue_drops_per_s_slope5`
- `active_connections` → `active_connections_mean5`, `active_connections_max5`, `active_connections_std5`, `active_connections_slope5`
- `tcp_retransmissions` → `tcp_retransmissions_mean5`, `tcp_retransmissions_max5`, `tcp_retransmissions_std5`, `tcp_retransmissions_slope5`
- `nf_active_flows` → `nf_active_flows_mean5`, `nf_active_flows_max5`, `nf_active_flows_std5`, `nf_active_flows_slope5`
- `nf_new_flows` → `nf_new_flows_mean5`, `nf_new_flows_max5`, `nf_new_flows_std5`, `nf_new_flows_slope5`
- `syn_ratio` → `syn_ratio_mean5`, `syn_ratio_max5`, `syn_ratio_std5`, `syn_ratio_slope5`

### 2.4 Lag features (24 = 8 signals × 3 lags)

They give the model the recent *history*, so it can see where the network came from, not only where it is. They need up to 30 s of history, so the first 30 s of every run are left empty and excluded from training.

| Lag | Meaning |
|---|---|
| `_lag5` | value 5 seconds ago |
| `_lag10` | value 10 seconds ago |
| `_lag30` | value 30 seconds ago (equal to the prediction horizon) |

- `throughput_mbps` → `throughput_mbps_lag5`, `throughput_mbps_lag10`, `throughput_mbps_lag30`
- `utilisation_pct` → `utilisation_pct_lag5`, `utilisation_pct_lag10`, `utilisation_pct_lag30`
- `queue_delay_ms` → `queue_delay_ms_lag5`, `queue_delay_ms_lag10`, `queue_delay_ms_lag30`
- `rtt_mean_ms` → `rtt_mean_ms_lag5`, `rtt_mean_ms_lag10`, `rtt_mean_ms_lag30`
- `loss_pct` → `loss_pct_lag5`, `loss_pct_lag10`, `loss_pct_lag30`
- `fwd_pps` → `fwd_pps_lag5`, `fwd_pps_lag10`, `fwd_pps_lag30`
- `active_connections` → `active_connections_lag5`, `active_connections_lag10`, `active_connections_lag30`
- `tcp_retransmissions` → `tcp_retransmissions_lag5`, `tcp_retransmissions_lag10`, `tcp_retransmissions_lag30`

Why these eight: they are the signals that move first before congestion (load, queue, delay, loss, retransmissions) plus flow and connection counts for the traffic type.

### 2.5 Causal congestion state (1)

| # | Feature | Why it is needed |
|---|---|---|
| 105 | `congested_causal` | 1 if the congestion condition (utilisation, queue or loss) has held for the last 3 seconds, using only the past. It replaces the true `current_congestion` label, which cannot be a feature because it depends on the next 2 seconds. Also drives the persistence baseline. |

## 3. The 52 raw per-second measurements

`fwd_` = traffic going left to right (switch s1 → s2), `rev_` = right to left.

| Group | Columns | Source | Need |
|---|---|---|---|
| Interface (10) | `fwd_/rev_` `tx_bytes_per_s`, `throughput_mbps`, `pps`, `utilisation_pct`, `dev_tx_dropped_per_s` | `ip -s link` | Load on each direction of the bottleneck. |
| Queue (14) | `fwd_/rev_` `queue_backlog_bytes`, `queue_backlog_packets`, `queue_delay_ms`, `queue_drops_per_s`, `random_drops_per_s`, `tc_drops_per_s`, `tc_loss_pct` | `tc -s qdisc` | Queue size, waiting time and drops: the direct congestion evidence. Random drops separate degradation from overflow. |
| Probe (7) | `probe_sent`, `probe_received`, `probe_loss_pct`, `rtt_mean_ms`, `rtt_min_ms`, `rtt_max_ms`, `jitter_ms` | ping every 0.2 s | Delay, delay variation and loss as an end user sees them. |
| Connections (2) | `active_connections`, `tcp_retransmissions` | `ss -ti` | Number of connections and TCP's reaction to loss. |
| VM resources (3) | `cpu_percent`, `ram_percent`, `ram_used_mb` | psutil | Experiment validity: a run is invalid if CPU reaches 100 %. Kept in the data but **not** a model input. |
| Packets by protocol (10) | `tshark_pkts`, `tshark_bytes`, `tshark_tcp_pkts`, `tshark_udp_pkts`, `tshark_icmp_pkts`, `tshark_dns_pkts`, `tshark_http_pkts`, `tshark_dns_queries`, `tshark_dns_responses`, `tshark_dns_errors` | TShark | Traffic mix per second; basis of the protocol shares and the SYN ratio denominator. |
| Flows (2) | `nf_active_flows`, `nf_new_flows` | NFStream | How many conversations exist and how fast new ones start. |
| Summary (4) | `throughput_mbps`, `utilisation_pct`, `queue_delay_ms`, `loss_pct` | derived: the worse of the two directions | The four inputs of the congestion rule. |

## 4. Columns that are not features

| Group | Columns | Need |
|---|---|---|
| Identifiers (7) | `experiment_id`, `scenario`, `repetition`, `seed`, `second`, `timestamp_s`, `experiment_status` | Track every row to its run; split by run; check timing. Never model inputs. |
| Labels (7) | `scenario_label`, `current_congestion`, `future_congestion`, `time_to_congestion`, `future_label_available`, `congestion_condition`, `labels_status` | The answers the models learn and are judged on. `time_to_congestion` is for evaluation only. |
| Validity flags | `features_complete`, `features_complete_nolag`, `valid_for_prediction`, `split` | Tell which rows may be used for training and which split they belong to. |

## 5. Which features mattered most (real results)

Random Forest scenario classifier, top features by importance: `nf_active_flows_mean5`, `share_udp`, `rtt_mean_ms_lag30`, `loss_pct_std5`, `rtt_mean_ms_lag5`. This is consistent with the design: flow counts separate crowds from floods, the UDP share separates floods, and delay history reveals degradation.

