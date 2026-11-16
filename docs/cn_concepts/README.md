# Computer Networks concepts used in NetGuard AI

This folder explains, concept by concept, the actual Computer Networks (CN) material this project
puts into practice — written for someone already past the absolute basics, aimed at explaining the
project confidently to a professor or examiner.

Every concept here is tied to a real, specific line of configuration or code in this repository —
nothing generic or textbook-only that we didn't actually build. Each section ends with a
**💡 Say this to your professor** box: a sharp, specific sentence that shows you understand *why*,
not just *what*.

## Files

1. **[01-topology-and-links.md](01-topology-and-links.md)** — the virtual network itself: topology,
   link types, bandwidth/delay/jitter/loss, MTU and fragmentation, NIC offloading, IP addressing.
2. **[02-switching-and-transport.md](02-switching-and-transport.md)** — switching (and why we chose
   *not* to use SDN), TCP vs. UDP, the 3-way handshake, application protocols, and network flows.
3. **[03-queueing-and-congestion.md](03-queueing-and-congestion.md)** — how congestion is actually
   created and measured on Linux: qdiscs, token buckets, drop-tail queues, bufferbloat, and the formal
   definition of "congested" this project uses.
4. **[04-measurement-and-monitoring.md](04-measurement-and-monitoring.md)** — how real network
   operators monitor a network, and which of those exact techniques this project reimplements.
5. **[05-future-work.md](05-future-work.md)** — concrete, CN-grounded protocols and techniques this
   project could add next (SDN/OpenFlow, NetFlow/IPFIX, ECN and modern queue management, QUIC/TLS,
   IPv6, QoS/DiffServ) — useful if a professor asks "what would you do with more time?"

## The one-paragraph version

We built a small, real (not simulated-on-paper) IP network on Linux — two switches, a slow
"bottleneck" link in the middle, several hosts on each side — using Mininet and Open vSwitch. We shape
that bottleneck's bandwidth, delay, jitter and loss with the kernel's own traffic-control subsystem
(`tc`, the same mechanism real routers and ISPs use), generate real TCP/UDP/HTTP/DNS/ICMP traffic
across it, and measure it with the same families of tools real network operators use (interface
counters, queue statistics, socket statistics, active ICMP probing, and passive packet/flow capture).
The machine-learning layer (explained separately in `../ml-explanations/`) is built entirely on top of
these CN measurements — it has no access to anything else.
