# NetGuard AI

Modular system that monitors traffic in an emulated network, flags unusual traffic and predicts congestion.
See `NetGuard AI — Project Documentation.pdf` for the full design.

**This repository currently implements Phase 1 only: network simulation and traffic generation.**
The experiment orchestrator, monitoring collectors, Parquet dataset pipeline and ML models are later phases and are
not part of this code.

All traffic, including the attack-like UDP flood, is generated **only between Mininet hosts inside the isolated
Ubuntu VM**. Nothing here changes network configuration outside Mininet.

## Layout

```
configs/
  topology.yaml        hosts per side, subnet, switch names
  links.yaml           bottleneck + access link parameters (bandwidth, delay, loss, queue)
  traffic.yaml         tool settings (iperf3/HTTP/DNS/ping/hping3 ports...) and safety limits
  scenarios/*.yaml     normal, burst, periodic, random, ramp, flash_crowd, udp_flood(+_hping3), degraded
netguard/
  config/              typed, validated models + YAML loader for topology / links / traffic
  scenarios/           scenario models, per-pattern option schema, loader, one-shot runner
  topology/            dumbbell.py (Mininet + OVS), tc.py (netem + tbf command builders / parsers)
  traffic/             patterns.py + planner.py (pure, seeded), commands.py (argv builders),
                       executor.py (runs a plan on Mininet hosts), workers/ (HTTP, DNS programs)
  utils/               validation helpers, logging, system pre-flight checks
  cli.py               python -m netguard {validate,list,plan,topology,run}
tests/
  unit/                run anywhere (Windows, Linux); no Mininet needed
  integration/         needs the Ubuntu VM, root, Mininet, Open vSwitch
```

## Topology

```
h1 ─┐                                           ┌─ h5
h2 ─┼─ s1 ══════ bottleneck (10 Mbit/s, 20 ms) ══════ s2 ─┼─ h6      (hosts_per_side: 3 or 4)
h3 ─┤                                           ├─ h7
h4 ─┘                                           └─ h8
      access links: 100 Mbit/s, no delay
```

* `s1`, `s2` are Open vSwitch bridges in `standalone` mode (learning switches, no SDN controller).
* Every interface is shaped with Linux `tc`: `netem` (delay, jitter, loss) as the root qdisc and a `tbf` child
  (rate + a small drop-tail queue of `queue_packets` packets). Plain Mininet `Link`s are used, not `TCLink`.
* **`delay_ms` is one-way and is applied to both directions**, so the default bottleneck has ≈ 40 ms RTT.
* The code refuses configurations where the access links are not faster than the bottleneck.
* After starting, the qdiscs are read back from the kernel and compared with the configuration; a mismatch aborts.

## Traffic generation

Scenarios are lists of *components*; each component has a *pattern* that lays its flows out over time. A scenario is
expanded into a deterministic list of timed events (`python -m netguard plan …` shows it without running anything).
Same scenario + same seed ⇒ identical events.

| Pattern | What it does | Main parameters |
|---|---|---|
| `NORMAL` | steady flows, random start offset per client | `rate_mbps` / `request_rate`, `clients`, `options.start_jitter_s` |
| `BURST` | optional base load + bursts that then drop back | `rate_mbps` (burst), `burst_interval_s`, `options.{base_rate_mbps,burst_duration_s,burst_count}`, `start_s` = first burst |
| `PERIODIC` | the same transfer repeated every period | `rate_mbps`, `options.{period_s,on_duration_s}` |
| `RANDOM` | random rate / duration / protocol / peer per flow slot | `clients` (slots), `seed`, `options.{rate_range_mbps,duration_range_s,gap_range_s,protocols}` |
| `RAMP` | staircase of aggregate load that crosses capacity at `onset_s` (default 60 s) | `options.{start_load,onset_load,max_load,onset_s,step_s}` as fractions of bottleneck capacity |
| `FLASH_CROWD` | many legitimate TCP clients arrive within `spread_s` | `clients`, `connections`, `start_s`, `duration_s`, `options.spread_s` |
| `UDP_FLOOD` | rate-limited high-rate UDP between Mininet hosts | `rate_mbps`, `packet_size`, `clients`, `start_s`, `duration_s` |

Common component fields: `name`, `pattern`, `tool` (`iperf3`, `hping3`, `ping`, `http`, `dns`), `protocol`
(`tcp`/`udp`; inferred for the other tools), `src`/`dst` (`left`, `right`, `all` or host names such as `h1`;
**IP addresses are rejected**), `rate_mbps` (iperf3/hping3, application payload, per client) or `request_rate`
(http/dns/ping, per second, per client), `packet_size` (payload bytes), `duration_s`, `start_s`, `clients`,
`connections` (parallel TCP connections / client threads), `burst_interval_s` (BURST only), `seed`.
A scenario may also set `link_overrides` (e.g. the `degraded` scenario adds 60 ms delay and 3 % loss).
The top-level scenario fields are `name`, `description`, `duration_s`, `seed`, `link_overrides`, `components`.

Tools: **iperf3** (TCP/UDP, one server port per event, `-J` JSON output), **hping3** (UDP only, always bounded by
`-c` and rate-limited with `-i`; never `--flood`, spoofing or random sources), **ping**, a **Python HTTP**
server/client (a new TCP connection per request) and a **DNS** responder plus a client that crafts and parses
queries with **Scapy** (sent over ordinary UDP sockets). Safety limits (max rate, clients, events, packet size,
hping3 packets/s) live in `configs/traffic.yaml`; before running, every involved host's real IP is checked to be
inside the private topology subnet.

Rate-shaping patterns are restricted to bandwidth tools (`iperf3`, `hping3`); `NORMAL` accepts all tools and
`FLASH_CROWD` accepts `iperf3` (TCP) and `http`.

## Installation (inside the Ubuntu VM)

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf3 hping3 iproute2 ethtool python3-venv python3-pip
sudo systemctl enable --now openvswitch-switch
sudo mn --test pingall          # sanity check of Mininet + OVS itself (should show 0% dropped)
```

```bash
cd NetGuardAI                   # wherever you copied this repository inside the VM
python3 -m venv --system-site-packages .venv      # system site-packages: the apt-installed `mininet` module
.venv/bin/pip install -r requirements.txt
```

Python ≥ 3.10 is required by the pinned dependencies (Ubuntu 22.04 or newer). Dependencies: PyYAML, Scapy, pytest
(pip, pinned in `requirements.txt`); Mininet, Open vSwitch, iperf3, hping3, iproute2 (`tc`), ethtool (apt).

## Running

Anything that does not need Mininet works on any OS (also on Windows):

```bash
python -m netguard validate                      # validate every YAML file and plan every scenario
python -m netguard list
python -m netguard plan ramp --events            # dry run: show the schedule and planned load
python -m netguard plan random --seed 7 --json
```

Inside the VM, as root (`sudo`), using the venv's Python:

```bash
sudo .venv/bin/python -m netguard topology --pingall --cli       # build + shape + verify, then Mininet CLI
sudo .venv/bin/python -m netguard run normal --results-json /tmp/normal.json
sudo .venv/bin/python -m netguard run ramp --duration 120        # shorter run for a quick check
sudo .venv/bin/python -m netguard run degraded --loss-pct 5      # CLI link overrides (also: --bandwidth-mbps, --delay-ms)
```

`run` builds the network, starts the planned traffic once and tears everything down. It records no dataset or
labels; that is the Phase 2 orchestrator. If a run is interrupted, clean up with `sudo mn -c`.

## Testing

Unit tests (no Mininet, no root, any OS):

```bash
python -m pytest
```

Integration test (Ubuntu VM, root; builds the real topology, checks connectivity, OVS, tc bottleneck settings and
TCP/UDP/HTTP/DNS/ping generation):

```bash
sudo mn -c
sudo .venv/bin/python -m pytest -m integration tests/integration -v -s -rs
```

Without root, Linux, Mininet or a running Open vSwitch the integration tests are skipped and `-rs` prints why.

## Notes and known limits

* Traffic rates are *application payload* rates; wire rates are a few percent higher. The RAMP default
  `onset_load: 1.05` crosses the bottleneck capacity at 60 s *as planned load*; the actual measured congestion onset
  must be checked against measurements in Phase 2 (the congestion label uses utilisation, queue delay and loss).
* hping3 sends UDP to a port with no listener; the receiving kernel answers with rate-limited ICMP "port
  unreachable" packets (small and expected).
* iperf3 servers are started per event just before the client; HTTP and DNS servers once per destination host. If
  worker start-up is slow on the VM, raise `executor.server_start_lead_s` in `configs/traffic.yaml`.
* The pinned versions in `requirements.txt` were the ones the unit tests were run with.
