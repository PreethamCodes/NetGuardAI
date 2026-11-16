# 2. Switching, transport protocols, and flows

## Switching: a plain learning switch, on purpose — not SDN

The two switches (`s1`, `s2`) are **Open vSwitch (OVS)** instances running in **`standalone` fail
mode**. In plain terms: they behave as ordinary Ethernet **learning switches** — they watch source MAC
addresses on incoming frames to learn which port each host is on, and forward frames accordingly. No
**SDN (Software-Defined Networking) controller** and no **OpenFlow** are involved.

This was a deliberate choice, not an oversight: our research question is about **congestion and traffic
behaviour on a link**, which exists identically whether the switches are "dumb" learning switches or
programmable OpenFlow switches taking instructions from a controller. Standalone mode keeps the
topology simple and removes an entire extra subsystem (the controller) that isn't needed to answer our
question — but OVS *can* run in controller mode, which is exactly the hook for future work (see
[05-future-work.md](05-future-work.md)).

💡 **Say this to your professor:** *"We used OVS in standalone mode deliberately, because our
congestion study doesn't need programmable forwarding — but the same switches could run in OpenFlow
mode with a controller, which is exactly how we'd extend this project to automated, network-level
mitigation instead of our current host-level demo."*

## TCP vs. UDP — and why a flood is built on UDP, not TCP

We generate both, side by side, specifically because they behave completely differently under load:

| | TCP | UDP |
|---|---|---|
| Connection | 3-way handshake (SYN → SYN-ACK → ACK) before any data | None — just send |
| Reliability | Retransmits lost data automatically (ARQ) | None — a lost packet is gone |
| **Congestion control** | **Yes** — slows itself down automatically when it detects loss (AIMD: Additive Increase, Multiplicative Decrease) | **None** |
| Used here for | Background "normal" traffic (bulk transfer, HTTP) | Background traffic **and** the flood |

The crucial, project-relevant fact is the congestion-control row: **TCP is self-limiting.** A real TCP
sender that starts seeing packet loss will automatically cut its own rate — which makes TCP a poor tool
for *deliberately* overwhelming a link, because the "attacker" would politely back off. **UDP has no
such mechanism.** `iperf3 -u` (and `hping3`) will keep sending at the configured rate regardless of how
much loss results. This is precisely why the UDP_FLOOD scenario (and real-world volumetric floods) are
built on UDP: it is the protocol that *doesn't know how to be polite*.

We observe the handshake and reliability mechanisms directly, not just in theory:

- **`tcp_retransmissions`** (read from `ss`, Linux's socket-statistics tool) counts TCP automatically
  resending data it believes was lost — a direct, measurable symptom of congestion/loss.
- **SYN packets** are counted from the capture (`bidirectional_syn_packets` from NFStream) and turned
  into `syn_ratio` — the fraction of this second's TCP packets that are connection-opening SYNs. A
  burst of many simultaneous SYNs (many new connections opening at once) is one of the clearest,
  oldest signatures of a crowd arriving or a connection-based flood starting.

💡 **Say this to your professor:** *"Our flood is UDP specifically because TCP's built-in congestion
control would undermine the attack by design — the sender would throttle itself. This is the same
reason real-world volumetric floods are almost always UDP-based, not TCP-based."*

## Application-layer protocols we actually generate

Three real application protocols run over the topology, each for a reason:

- **HTTP** — a minimal real HTTP server/client pair we wrote (`http.server`-style), generating genuine
  request/response traffic on a configured port, so "ordinary browsing" has a real signature to learn
  from and classify.
- **DNS** — real UDP queries on port 53 against a small fixed list of test domains, because DNS
  look-ups are one of the most universal signs of "someone is using the internet normally."
- **ICMP** (`ping`) — used *as a measurement tool*, not traffic: every 0.2 s we send an ICMP echo
  request and time the echo reply, which is how round-trip time, jitter and probe-level loss are
  measured, independent of whatever TCP/UDP traffic is also on the link.

**How we tell protocols apart in a capture:** by the IP protocol number and the well-known port, exactly
how real traffic classifiers and firewalls do it at this level — `ip.proto == 6` is TCP, `17` is UDP,
`1` is ICMP; a TCP packet to/from the configured HTTP port is counted as HTTP; a UDP packet to/from port
53 is counted as DNS (and further split into query vs. response vs. error using the DNS header's own
response flag and reply code). This is **port/protocol-number-based classification** — simple,
fast, and exactly what a basic firewall or NetFlow-style tool does; it does not inspect encrypted
payloads (see [05-future-work.md](05-future-work.md) for what changes once traffic is encrypted).

💡 **Say this to your professor:** *"Our protocol classification uses the IP protocol number and
well-known ports — the same lightweight technique real routers and firewalls use for traffic
accounting, before anything more expensive like deep packet inspection is needed."*

## Flows and the 5-tuple

A **flow** is the standard way network monitoring systems (NetFlow, IPFIX, sFlow) group packets that
belong to "the same conversation": all packets sharing the same **5-tuple** — source IP, destination
IP, source port, destination port, and protocol — are one flow, usually tracked **bidirectionally** (both
directions of a TCP/UDP conversation as a single record). We use **NFStream**, an open-source
flow-exporter library, to turn our packet capture into exactly this kind of flow table after each
experiment, with an **idle timeout of 120 s** (a flow with no packets for 2 minutes is considered
finished) and an **active timeout of 1800 s** (a very long-lived flow is still cut into fresh records
periodically) — the same two timeout concepts every flow-monitoring system uses.

From that flow table we derive two features every second: **how many flows are currently alive**
(`nf_active_flows`) and **how many just started** (`nf_new_flows`). These turned out to be two of the
single most useful numbers in the whole project (see `../ml-explanations/02-features.md`) — a crowd or
a flood both show up as an unusual *number of conversations*, which is a flow-level property, not
something visible from byte counts alone.

💡 **Say this to your professor:** *"We didn't invent our own definition of 'flow' — we used the
industry-standard bidirectional 5-tuple flow model with idle/active timeouts, the same concept behind
NetFlow and IPFIX, which is why `active_connections`/flow counts are such strong, portable features."*
