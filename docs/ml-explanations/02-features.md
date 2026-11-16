# The 105 features, in plain English

A "feature" is just one number that describes one second of network activity. We don't hand the models
the raw, messy measurements straight off the wire — we first turn them into **105 cleaner, more
tellable numbers** per second. This is the standard ML step called *feature engineering*: giving the
model the clues that make the pattern obvious, instead of making it rediscover the clues itself.

## How the 105 numbers are built (the construction methods)

Think of each second of the network like one check-up on a patient. We don't just note today's
temperature — we also look at the trend. That's exactly what we do here, four times over:

1. **15 "vitals"** — the raw signals measured every second: how much data is flowing (`throughput`),
   how full the network is (`utilisation`), how long packets wait in the queue, how much gets dropped
   or lost, round-trip delay and its jitter, how many conversations are open, how many are brand new,
   how many packets per second, and how many retransmissions (signs of real trouble).
2. **5 "what kind of traffic is this made of" shares** — the fraction of this second's packets that are
   TCP, UDP, ICMP, DNS or HTTP. A flood is almost pure UDP; ordinary browsing is a TCP/DNS/HTTP mix.
3. **60 "last 5 seconds" numbers** — for each of the 15 vitals, four follow-up questions: *what's the
   average over the last 5 seconds* (smooths out noise), *what's the worst/highest moment* (catches a
   short spike), *how jumpy has it been* (steady vs. erratic), and *is it trending up or down* (the
   single most useful of the four, since congestion builds up gradually — a rising trend is an early
   warning the instant reading alone can't give you).
4. **24 "look-back" numbers** — what 8 of the most important vitals looked like **5, 10 and 30 seconds
   ago**. This lets a model compare "where we are" with "where we just were" without us having to
   explicitly tell it "this is a trend" — the model works that out itself from the two numbers.
5. **1 "has this already been going on" flag** — whether the network has *already* been struggling for
   the last few seconds, using only information from the past. (The real, forward-looking answer —
   "will it still be struggling 2 seconds from now" — is deliberately **never** given to the model as
   an input: that would be peeking at the future, which is cheating and would make the results fake.)

A few more honesty rules we kept throughout:
- The **first 30 seconds of every recording are thrown away** — there isn't enough history yet for the
  "look-back" numbers, so we never fill them in with a guess; we simply don't use that part.
- We measured **52 raw numbers** in total per second (interface counters, queue stats, ping probes, VM
  CPU/RAM, packet captures, flow records); only **some** became model features. Things like the test
  computer's own CPU/RAM load were measured but deliberately **excluded** — they describe the computer
  running the experiment, not the network, so including them would teach the model the wrong lesson.
- Before training, every number is rescaled (so "40 milliseconds" and "9 million bytes" are on a
  comparable scale) using only the **training** data's own range — like calibrating a ruler once on
  practice problems, then using that same ruler, unchanged, to measure the real exam.

## The 12 features that mattered most

Rather than guess which numbers matter, we asked each trained model directly (every model we use can
report which inputs it actually leaned on). These are the ones that came up **again and again across
different models** — the strongest, most cross-checked clues in the whole dataset:

| # | Feature | In plain English | Why it matters |
|---|---|---|---|
| 1 | `nf_active_flows_mean5` | Average number of ongoing conversations over the last 5s | The single most useful number overall — many at once means a crowd or a flood |
| 2 | `share_udp` | Fraction of this second's traffic that is UDP | Floods and stress-tests are almost pure UDP; ordinary use is a TCP/DNS/HTTP mix |
| 3 | `loss_pct_std5` | How much packet loss has swung around in the last 5s | Steady loss and spiky loss tell different stories about what's wrong |
| 4 | `rtt_mean_ms` | Round-trip delay, right now | The most direct, immediate sign that the link is struggling |
| 5 | `rtt_mean_ms_mean5` | Average round-trip delay over the last 5s | Smooths out one noisy ping to show the real recent level |
| 6 | `rtt_mean_ms_lag30` | Round-trip delay exactly 30 seconds ago | Lets the model compare "now" with "half a minute back" to catch a slow build-up |
| 7 | `nf_active_flows_max5` | The busiest moment (most conversations at once) in the last 5s | Catches a short, sharp burst that an average would smooth away |
| 8 | `share_dns` | Fraction of traffic that is DNS look-ups | A tell-tale sign of ordinary browsing; near-absent during a flood |
| 9 | `active_connections_mean5` | Average open TCP connections over the last 5s | Many simultaneous connections is a crowd/flood signature |
| 10 | `nf_new_flows_mean5` | How many brand-new conversations started per second, last 5s | A rush of new connections is the clearest sign of a crowd arriving or a flood starting |
| 11 | `share_icmp` | Fraction of traffic that is ICMP (ping-type) packets | Separates ping-based probing/flooding from ordinary web traffic |
| 12 | `rtt_mean_ms_lag5` | Round-trip delay 5 seconds ago | The very recent past, useful for catching a fast-moving change |

(Full ranked lists per model, and all 105 features individually explained, are in
[`../FEATURES.md`](../FEATURES.md).)

**One pattern worth telling your peers:** round-trip delay (`rtt_mean_ms` and its 3 variants above)
shows up **4 of the 12 times** — more than any other single "vital". Delay is the network's earliest,
most honest symptom: it starts rising before loss does, before the obvious signs show up.

## What the dataset actually looks like

The finished dataset is one big table: **4,500 rows × 105 feature columns** (plus a handful of ID and
label columns) — built from 5 situations (`normal`, `ramp`, `flash_crowd`, `udp_flood`, `degraded`),
each run 3 times, 300 seconds each.

Here is a **real slice** of it — the `ramp` test run, right at the moment traffic crosses from fine
into congested (second 62 is exactly where our congestion rule first fires):

```
 second  throughput_mbps  utilisation_pct  queue_delay_ms  loss_pct  rtt_mean_ms  nf_active_flows  share_udp  congested_causal
     58            10.07           100.71           14.90      0.00        53.54              9.0       0.99               0.0
     59            10.05           100.46           18.88      0.00        58.42              9.0       0.99               0.0
     60             9.53            95.27           55.64      0.00        63.80             17.0       0.87               0.0
     61             9.95            99.53           57.63      7.76        98.42              9.0       0.99               0.0
     62            10.00            99.96           58.62      8.01        98.10              9.0       0.99               1.0   <- congestion rule fires here
     63            10.06           100.64           58.62      7.99        97.72              9.0       0.99               1.0
     64            10.24           102.39           57.63      7.89        98.46              9.0       0.99               1.0
```

Read left to right: the link is already near 100% full by second 58, but it only gets *labelled*
congested at second 62 — once delay and loss have *also* crossed their thresholds and stayed there for
3 seconds in a row. That gap between "it's clearly full" and "it's officially congested" is exactly
what Job 3 (congestion prediction) is trying to catch early.

This table shows only 7 of the 105 feature columns for readability — a real row also carries the other
98 (the 5-second statistics, the 5/10/30-second look-backs, and the protocol shares) described above.
