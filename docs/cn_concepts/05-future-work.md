# 5. Future protocols and techniques — concrete next steps

Every item here is a specific, real networking protocol or mechanism that extends something already
built in this project — not a generic "add more AI" suggestion. Good material for "what would you do
next?"

## 1. A real SDN controller (OpenFlow) for automated mitigation

**Now:** our switches run in plain learning-switch mode (no controller), and our live attack demo
"mitigates" the flood with a hand-run `tc` rate-limit command on the attacker's own interface — a
host-level hack for demonstration, not a network-level fix.

**Next:** run the same OVS switches in **controller mode** with a real **SDN controller** (e.g. Ryu or
ONOS) speaking **OpenFlow**. The moment our model flags a flood, instead of shell-scripting `tc` on one
host, we'd push an OpenFlow rule straight to the switch — *drop* or *rate-limit* traffic matching the
attacker's flow (5-tuple), at the exact point it enters the network. This is the real, production
version of "detect then mitigate": the network itself becomes programmable, not just the demo script.

## 2. NetFlow / IPFIX / sFlow instead of our own collectors

**Now:** our flow statistics come from NFStream post-processing a local packet capture, and our
counters come from polling `ip`/`tc`/`ss` by hand over an SSH-equivalent shell.

**Next:** have real routers/switches **export** flow records directly using the standard **NetFlow v9 /
IPFIX** protocols (or **sFlow** for sampled, lower-overhead export), the way production ISPs and
enterprise networks actually do it. This removes the need for our custom collector scripts entirely on
real hardware, and is the natural step if this project ever moved off a single Mininet VM onto real
switches.

## 3. ECN and modern Active Queue Management (replacing plain drop-tail)

**Now:** our bottleneck uses a plain drop-tail `tbf` queue — full means drop, nothing smarter — which is
exactly the mechanism behind the **bufferbloat** problem we already discuss in
[03-queueing-and-congestion.md](03-queueing-and-congestion.md).

**Next:** two real, deployed improvements:
- **ECN (Explicit Congestion Notification)** — instead of silently dropping a packet, a router
  *marks* it to say "you're close to congestion," letting an ECN-aware TCP slow down **before** any
  loss happens at all. Adding ECN support to our link would let us study "congestion warned *inside the
  network itself*" versus our current "congestion inferred *from the outside*, by measurement."
- **Modern AQM algorithms (CoDel, FQ-CoDel, PIE)** — designed specifically to defeat bufferbloat by
  dropping (or marking) packets based on *how long* they've been queued, not just *whether* the queue is
  full. Comparing our plain drop-tail queue against FQ-CoDel on the same topology would be a direct,
  concrete experiment: does a smarter queue discipline change how early our congestion model can warn?

## 4. Encrypted and QUIC/HTTP-3 traffic

**Now:** our protocol classification works because our traffic is unencrypted and uses well-known ports
(plain HTTP, plain DNS) — a reasonable simplification for a lab dataset, but not how most real traffic
looks today.

**Next:** two real complications worth tackling on purpose:
- **TLS/HTTPS** — port/payload-based classification stops working once content is encrypted; the
  realistic replacement is **metadata-based** classification (TLS handshake fields, the unencrypted
  **SNI** server name, or **JA3/JA3S** TLS fingerprinting) instead of looking inside the payload.
- **QUIC (HTTP/3)** — QUIC runs **over UDP**, by design, for ordinary encrypted web traffic. That
  directly challenges one of our strongest signals (`share_udp` — currently a strong clue that traffic
  is flood-like): a network with heavy QUIC/HTTP-3 adoption would have *lots* of legitimate UDP, and
  our current UDP-heavy-means-suspicious intuition would need to be re-learned or supplemented with
  flow-shape features that don't depend on the transport protocol at all.

## 5. IPv6

**Now:** the whole topology is IPv4-only, on a private `10.0.0.0/24` range.

**Next:** a dual-stack (or IPv6-only) version of the same dumbbell, to check whether the same features
and models transfer — IPv6 changes addressing and some header fields, but the underlying congestion
physics (queueing, delay, loss) is unchanged, so this is really a test of how *address-family-agnostic*
our feature design actually is.

## 6. QoS / DiffServ — prioritising legitimate traffic instead of only punishing the attacker

**Now:** our only "response" to a detected flood is to throttle the attacker specifically.

**Next:** mark legitimate traffic classes with **DSCP (Differentiated Services Code Point)** values and
give them priority queuing during congestion — the standard **DiffServ** model used for things like
prioritising voice/video over bulk downloads. Rather than only *punishing* the attacker, this protects
*everyone else* even before an attacker is positively identified — a more realistic, defence-in-depth
mitigation strategy.

## 7. Bigger, more realistic topologies — and a true DDoS

**Now (an openly stated limitation):** one VM, one dumbbell topology, one bottleneck, and our flood is
a **single-source DoS**, not a distributed one.

**Next:**
- A **fat-tree or spine-leaf** topology (the standard datacenter network shape) with **multiple**
  bottlenecks, to see whether congestion at one link can be told apart from congestion at another.
- A **wireless link emulation** (bursty, correlated loss, unlike our current independent random-loss
  model) to test whether our "congestive loss vs. random loss" distinction still holds up against a
  more realistic, bursty loss pattern.
- A genuine **multi-source DDoS** (several attacking hosts, optionally with **reflection/amplification**
  via DNS or NTP — a different, very real attack class that multiplies a small request into a large
  response aimed at the victim) to test whether our anomaly/flow features still work when the attack is
  spread across many sources instead of concentrated in one.
