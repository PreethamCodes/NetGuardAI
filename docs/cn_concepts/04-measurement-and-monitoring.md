# 4. Measurement and monitoring — the same techniques real network operators use

Every one of our 52 raw per-second measurements comes from a tool that has a direct, real-world
counterpart in actual network operations. Nothing here is a research-only trick.

| Tool we use | CN concept it realises | What it gives us |
|---|---|---|
| `ip -s link` | **Interface counters** — the same byte/packet counters exposed by SNMP `IF-MIB` on every real router/switch | throughput, packets/s, utilisation |
| `tc -s qdisc` | **Queue statistics** — backlog, drops — exposed by the same qdisc layer real Linux-based routers use | queue backlog (bytes), queue delay, drop rate |
| `ss -tinH` | **Socket statistics** — per-TCP-connection kernel state | open connections, retransmission counts |
| `ping` (active probing) | **Active measurement** — you inject your own test traffic (ICMP echo) to measure the path, instead of just watching what's already there | RTT, jitter (RFC 3550), end-to-end loss |
| `tshark` (packet capture) | **Passive measurement** — a mirror/tap-style full packet capture, read offline | protocol mix, SYN counts, byte-accurate traffic composition |
| NFStream (flow export) | **Flow-based monitoring** — the NetFlow/IPFIX model: group packets into conversations with a 5-tuple + timeouts | active/new flow counts per second |
| `psutil` | **Host/system telemetry** — CPU/RAM of the measuring VM itself | sanity-checks that a run wasn't invalidated by host overload (kept *out* of the ML features — see `../ml-explanations/02-features.md`) |

## Active vs. passive measurement — a real, examinable distinction

- **Active measurement** means *generating* traffic specifically to measure the network (our `ping`
  probes, sent every 0.2 s, exist purely to measure RTT/jitter/loss — they carry no useful payload).
- **Passive measurement** means *observing* traffic that was going to exist anyway (our packet capture
  and flow export don't add anything to the network — they just record what's already there).

We deliberately use **both**, because they answer different questions: active probing tells you what the
path currently *feels like* to a user (end-to-end, synthetic); passive capture tells you exactly
*what traffic actually happened* (real, but after the fact, since NFStream processes the capture offline
once the experiment is over, not in real time).

💡 **Say this to your professor:** *"We combined active probing and passive capture on purpose — active
(ping) gives a real-time, user-facing signal; passive (packet capture, flow export) gives ground-truth
detail about what traffic actually caused that experience. Real network operations centres use exactly
this combination."*

## A shared clock — why timing discipline matters

All seven collectors (interface, queue, connections, probe, resources, packet capture, flow export) are
synchronised to **one shared wall-clock timeline**, with a 5-second pre-roll before traffic starts and a
tail afterwards so no measurement is cut off mid-event. Without this, "second 60" from one tool and
"second 60" from another could silently be half a second apart — enough to misalign a label with the
measurement that's supposed to explain it. This is the same reason real distributed-monitoring systems
care about clock synchronisation (NTP, PTP) — ours is simpler (one VM, no network time skew to correct
for), but the underlying principle — *a measurement is meaningless without a trustworthy, shared
timestamp* — is the same.

💡 **Say this to your professor:** *"Every collector shares one anchored wall-clock timeline specifically
so that 'second 60' means the same physical instant to every single measurement — the same timing
discipline distributed monitoring systems need clock synchronisation (NTP/PTP) for, just at a smaller,
single-host scale."*

## Live monitoring vs. historical replay

The backend supports both a **live** mode (a running Mininet experiment posts one real measurement per
second, scored immediately) and a **replay** mode (a previously recorded run played back). This mirrors
a very standard real-world network-operations distinction: a **live dashboard/alerting pipeline** versus
a **historical analytics/forensics pipeline** — the same measurements, the same scoring logic, but two
different operational use cases (watch it happen vs. investigate what happened).
