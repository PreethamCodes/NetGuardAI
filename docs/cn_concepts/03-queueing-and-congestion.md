# 3. Queueing and congestion — how it's actually created and defined

## The qdisc hierarchy: netem (parent) + tbf (child)

Every shaped interface gets **two chained Linux queueing disciplines (qdiscs)**, not one:

```
root  (handle 1:)   netem   — delay, jitter, random loss     (emulates the WAN link itself)
 └─ child (handle 10:) tbf  — rate limit + burst + queue      (emulates the link's finite capacity)
```

This is the layout the kernel's own `netem` documentation recommends for combining delay with rate
limiting, and the order matters: traffic is delayed/jittered/randomly-dropped by `netem` *first*, then
handed to `tbf`, which is the piece that actually enforces the bandwidth cap and holds packets in a
queue when they arrive faster than the link can send them.

💡 **Say this to your professor:** *"We use two separate qdiscs because they model two separate real
phenomena: `netem` emulates propagation delay and random link-level loss, while `tbf` emulates the
link's finite capacity and its queue — conflating them into one qdisc would conflate two different
causes of 'bad network'."*

## Token Bucket Filter (TBF) — rate limiting, explained simply

A **token bucket** is the standard algorithm for enforcing "no more than X Mbit/s, but allow short
bursts": imagine a bucket that fills with "tokens" at a steady rate (our configured bandwidth); sending
a packet costs tokens equal to its size; if the bucket is empty, the packet must wait. The bucket's
**capacity** (`burst`) is how much of a momentary burst above the average rate is allowed before the
limit truly bites. Any packet that arrives when the attached queue is also full is simply dropped —
this is a classic **drop-tail queue** (new arrivals are rejected once the queue is full; nothing clever
about *which* packet to drop).

- **Queue size:** 50 packets at the bottleneck (`queue_packets: 50`), which at 10 Mbit/s works out to
  roughly **60 ms** worth of queueing delay if the queue is completely full — this single number is
  *why* our `queue_delay_ms` feature can reach the ~55–60 ms values you see in a congested second.

💡 **Say this to your professor:** *"Our 50-packet queue isn't an arbitrary number — it directly sets
the maximum possible queueing delay (~60 ms at 10 Mbit/s), which is the ceiling we see in our own
measured `queue_delay_ms` during congestion."*

## Bufferbloat — a real, named phenomenon we deliberately reproduce

A drop-tail queue that is *too generous* causes a well-known, named real-world problem called
**bufferbloat**: instead of dropping packets quickly (which would let senders notice and react), a
large buffer lets packets queue for a long time before anything is dropped, so delay quietly climbs to
very high values before loss ever appears. Our own 50-packet queue is a small-scale, deliberately
simplified version of exactly this trade-off: it's generous enough that delay rises substantially (our
`queue_delay_ms` feature) *before* loss kicks in, which is precisely why our congestion rule looks at
**both** delay *and* loss, not loss alone — a shallower queue would show loss immediately and make delay
a less useful early-warning signal.

💡 **Say this to your professor:** *"Our congestion label requires high delay *or* high loss, sustained
for 3 seconds — not loss alone — because our queue is deep enough to reproduce a small-scale version of
bufferbloat, where delay is the earlier symptom and loss only follows once the buffer is completely
full."*

## Two different causes of packet loss — and we model them separately

A genuinely subtle point most intro courses skip: **not all packet loss has the same cause.**

| Cause | How we create it | Which scenario |
|---|---|---|
| **Congestive loss** | The `tbf` queue is completely full, so a new packet has nowhere to go and is dropped | RAMP, UDP_FLOOD, FLASH_CROWD (whenever the link saturates) |
| **Random / link-error loss** | `netem`'s own `loss_pct`, applied independently of how full the queue is — modelling a lossy radio link, a flaky cable, etc. | DEGRADED (3% loss, plus +60 ms extra delay, **without** overloading the link) |

We deliberately built a scenario (**DEGRADED**) where the link is **not** overloaded at all, yet still
has real loss and extra delay — specifically so the dataset contains an example of "bad network
experience that is *not* congestion", so our models (and our congestion rule) aren't tricked into
treating *any* loss or delay as proof of overload.

💡 **Say this to your professor:** *"We deliberately separated congestive loss (a full queue) from
random link loss (netem's own loss, independent of queue state) into two different scenarios, so our
congestion detector learns that 'bad numbers' and 'congestion' are not the same thing."*

## What "congested" formally means in this project

Putting the pieces together, our own working definition of *congestion* (used to generate every label
in the dataset) is:

> **utilisation ≥ 90% AND (queue delay ≥ a calibrated threshold OR loss > 1%), sustained for 3
> consecutive seconds.**

This mirrors the standard, textbook description of congestion as a chain reaction — rising utilisation
→ rising queueing delay → eventual loss and retransmission — except we insist on **all** of utilisation,
delay-or-loss, **and** persistence (3 seconds, not one noisy spike) before calling something
"congested", which keeps a single noisy measurement from mislabeling the whole dataset.

💡 **Say this to your professor:** *"Congestion in our project isn't a single threshold on one number —
it's a compound, persistence-checked condition across utilisation, delay and loss, because that's how
congestion actually behaves physically: a chain, not a single symptom."*

## Round-trip time and jitter — measured the standard way

- **RTT** comes from real ICMP echo request/reply timing (`ping`), not from `netem`'s configured delay —
  so it reflects the *actual* link state (including any current queueing), not just the static
  configuration.
- **Jitter** is computed exactly as **RFC 3550** (the RTP/real-time-media standard) defines it: the mean
  absolute difference between consecutive received RTT samples — the same formula voice/video
  conferencing software uses to decide how large a playout buffer it needs.

💡 **Say this to your professor:** *"Our jitter calculation follows RFC 3550, the same standard used by
real-time voice and video protocols — it's not a made-up 'variance' metric, it's the metric the
industry actually standardised for exactly this purpose."*
