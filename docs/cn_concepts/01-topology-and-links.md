# 1. The network: topology, links, and physical-layer details

## Dumbbell topology

```
h1 --\                         /-- h5
h2 ---- s1 ===(bottleneck)=== s2 ---- h6
h3 --/                         \-- h7
h4 -/                           \-h8
```

A **dumbbell topology**: two groups of hosts, each connected to their own switch over fast **access
links**, with the two switches joined by a single, deliberately slow **bottleneck link**. This is the
standard topology used in networking research for studying congestion, because it guarantees that
traffic from *every* host must compete for the *same* shared resource — there's nowhere else for it to
go. That shared competition is exactly what "congestion" means.

- **Access links:** 100 Mbit/s, no added delay/loss — representing a fast local/LAN segment.
- **Bottleneck link:** 10 Mbit/s, 20 ms one-way delay, a 50-packet queue — representing a slow,
  constrained uplink (think: a home broadband connection, or a slow WAN/satellite hop), deliberately
  made the *only* path between the two sides.

💡 **Say this to your professor:** *"We didn't just simulate congestion with a formula — we built a
real IP network with a genuine shared-resource bottleneck, the same structural pattern (dumbbell)
networking research has used for decades to study congestion and fairness."*

## Bandwidth, delay, jitter, loss — and how they're actually applied

Each link's four parameters are applied with Linux's own **traffic control (`tc`)** subsystem — the
exact mechanism real routers, and tools like `wondershaper` or ISP-side shaping, use:

- **Bandwidth** — enforced with a **token bucket filter (tbf)**: see
  [03-queueing-and-congestion.md](03-queueing-and-congestion.md) for how this actually works.
- **Delay and jitter** — injected with **`netem`** ("network emulation"), the Linux kernel module built
  specifically to make a link *behave* like a real WAN link, even on a VM.
- **Loss** — also `netem`: a configurable *random* chance of dropping a packet, independent of how full
  the queue is (see below — this is a different kind of loss from congestion loss).

**One-way delay vs. round-trip time (RTT):** the bottleneck's `delay_ms: 20` is **one-way**, and is
applied to *both ends* of the link (because `tc` only shapes outgoing/egress traffic, so shaping just
one direction would leave the other unshaped). Two hops of 20 ms, there and back, is why a healthy ping
across this network reads **~40 ms**, not 20 ms — a detail that separates someone who actually built the
network from someone quoting a number.

💡 **Say this to your professor:** *"Our ping RTT of ~40 ms isn't a parameter we typed in directly — it
falls out correctly from a 20 ms one-way delay applied to both directions of the link, because `tc`
only ever shapes egress traffic."*

## MTU and avoiding fragmentation

The link MTU is 1500 bytes (standard Ethernet). Our own UDP flood traffic is capped at **1472 bytes**
of payload — not a round number by accident:

```
1500 (Ethernet MTU) − 20 (IPv4 header) − 8 (UDP header) = 1472 bytes
```

Staying under this means every packet we generate fits in **one** Ethernet frame — no IP fragmentation,
so the packet counts our collectors report match the number of datagrams actually sent, with no
double-counting from split fragments.

💡 **Say this to your professor:** *"We deliberately bounded our packet size to 1472 bytes so every
generated packet is exactly one IP fragment — this is the standard MTU-minus-headers calculation, and
it keeps our own packet-count measurements honest."*

## Disabling NIC offloads (TSO/GSO/GRO)

Before shaping a link, we explicitly turn off **TSO, GSO and GRO** (`ethtool -K <dev> tso off gso off
gro off`) — hardware/driver features that let the OS hand the NIC one large "super-packet" and have it
split (or merge received fragments) in hardware for speed. On a *real* NIC this is a welcome
performance optimisation; inside a shaped virtual link it actively works against us, because `tc` and
our packet captures see traffic *after* these transformations — a shaping rule meant to apply to
1500-byte packets would instead see a handful of oversized ones, and our per-packet counters would be
wrong. We disable them so what the kernel shapes, and what we measure, is the real, final, on-the-wire
packet.

💡 **Say this to your professor:** *"Offload disabling is the kind of practical detail that separates a
textbook network diagram from one that actually measures correctly — it's not in most courses, but it's
necessary once you shape and capture real packets rather than just simulate them."*

## Addressing

A single private IPv4 subnet, **`10.0.0.0/24`** (RFC 1918 private address space — exactly the same
range home routers use), with host `hK` assigned `10.0.0.K`. A `/24` prefix gives a 255.255.255.0
netmask — 254 usable host addresses, far more than the 6–8 hosts we actually use, left that way simply
because it's the conventional, unambiguous choice for a small lab network.

💡 **Say this to your professor:** *"Every host's IP is derived programmatically from its name (`h5` →
`10.0.0.5`), so the topology size (3 or 4 hosts per side) is a config value, not something hardcoded
host-by-host."*
