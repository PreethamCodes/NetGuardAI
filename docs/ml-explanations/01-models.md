# The 3 jobs, the models we picked, and what we learned

We gave the computer three separate jobs. For each job we tried a few standard, well-known algorithms
and kept whichever one actually worked best **for that job** — which, twice out of three, was *not*
the most "powerful"-sounding option.

---

## Job 1 — "What kind of traffic is this second?" (classification)

**In plain English:** look at one second of network activity and guess which of 5 situations it is —
ordinary traffic, a gradual overload, a sudden crowd of genuine users, a flood attack, or a degraded
link.

**Tried:** Decision Tree, Random Forest, XGBoost — three well-known "pattern matchers" that all learn
by example, just with different levels of sophistication (a Decision Tree is one tree of yes/no
questions; Random Forest and XGBoost each combine hundreds of small trees and vote).

**Picked for the live system: XGBoost.**
Random Forest was very slightly more accurate (90.8% vs. 90.1% on data it had never seen), but it was
**~230× slower per second** (≈100 ms vs. ≈0.4 ms) — because it has to ask hundreds of large trees
instead of hundreds of small, cheap ones. When you must score *every single second forever*, a tiny
accuracy difference is not worth that much extra time.

**What we learned:**
- All three got about **9 out of 10 seconds right** on data they had never seen — a solid result.
- The one it struggles with is **UDP_FLOOD** (correctly caught only ~6 out of 10 flood seconds). The
  model often confuses a flood with **RAMP** (a gradual, legitimate load increase) — because, from pure
  numbers alone, "a lot of fast UDP traffic" looks similar whether it's a stress test or an attack. This
  mirrors a genuine real-world problem: telling a surge from an attack by traffic shape alone is hard.

---

## Job 2 — "Is something unusual happening right now?" (anomaly detection)

**In plain English:** instead of teaching the computer what "trouble" looks like (trouble comes in too
many different shapes), we only ever showed it **ordinary, trouble-free traffic** (≈4.5 minutes of it)
and asked it to notice whenever the *current* second looks meaningfully different from that. This is
exactly what "anomaly detection" means: *learn normal, flag different.*

**Tried:** Isolation Forest and LOF ("Local Outlier Factor") — two standard ways of spotting the odd
one out in a pile of normal examples.

**Picked: Isolation Forest.**
It catches **100% of every unusual second** in our test data (it misses nothing), at the cost of a
false alarm on about **12% of ordinary seconds**. LOF is a little more precise but still misses a few
real cases. For a security tool, missing a real incident is worse than one extra false alarm to check —
so we kept the more sensitive one.

**What we learned (the honest part):**
- It is *very* sensitive — on top of catching every attack, it also flags **100% of FLASH_CROWD**
  seconds (a sudden but perfectly legitimate crowd of real customers), because a genuine crowd *also*
  looks statistically different from quiet, ordinary traffic.
- So this model alone only tells you "something is different here," not "this is an attack." We added a
  second, simple **rule-based layer** on top that reads the raw numbers (how much is UDP, how many
  distinct conversations, how much delay) and offers a plain-English guess — "flood-like", "legitimate
  burst", "degradation", etc. That guess agrees with the true situation only about **half the time**
  (it tends to call a gradual overload "flood-like" too, since both are UDP-heavy) — an honest
  limitation we keep visible in the dashboard rather than hide.

---

## Job 3 — "Will the network jam in the next 30 seconds?" (congestion prediction)

**In plain English:** using only what has already happened (never the future), predict whether a new
jam will start sometime in the next half-minute — early enough that someone could actually do something
about it.

**Tried:** Logistic Regression (a simple straight-line rule), Random Forest and XGBoost (same as Job
1), plus three "common sense" baselines for comparison: *persistence* ("if it's jammed now, assume it
stays jammed"), *utilisation over 80%*, and *utilisation trending up*.

**Picked: Logistic Regression — the simplest option of the three.**
It had the best overall balance (F1 score 0.65) and almost never cried wolf (87% of its warnings were
right), beating both "fancier" models at this specific job. The reason a simpler model won is itself a
useful lesson: real congestion *starting* is a rare event — our test data only has **6 such moments** to
learn from. A simple model is like learning a general rule from a few examples; a complex model is more
like **memorising those few examples instead of understanding them** — it looks great on the data it
memorised and worse on anything slightly new. With so few real examples, the simple rule generalised
better.

**What we learned:**
- It **beat every common-sense baseline**, including the "just watch if utilisation is high" rule
  (F1 0.65 vs. 0.33) — using the shape of recent history (several numbers, not just one threshold)
  genuinely helps.
- It warned **30 seconds ahead of 5 out of the 6** real jams in the test data. The one baseline that
  "reacts fastest" (persistence) **cannot warn ahead of time at all** by definition — it only ever
  confirms what's already happening.
- With only 6 real events to test against, this result is **promising, not proven** — a fair,
  important caveat we state everywhere else in this project too.

---

## The big picture, across all three jobs

- **Simpler isn't automatically worse.** With few rare real-world examples (floods, real jam onsets),
  the simpler model (Logistic Regression) beat the fancier ones; with plenty of examples (traffic
  classification), the fancier one (XGBoost) pulled ahead — but speed still mattered more than a tiny
  accuracy edge.
- **A single number (like "utilisation now") always arrives late.** Across every job, numbers that
  describe a *trend* or a *recent history* (rolling averages, "what was this 30 seconds ago") mattered
  more than the instant reading — because congestion builds up gradually, not instantly.
- **Which protocol is involved is one of the strongest single clues** for telling ordinary traffic from
  a flood or a crowd — see [02-features.md](02-features.md) for exactly which numbers mattered most.
- **None of these models act on their own.** Every one of them only scores and explains; a human always
  decides what to do next (see `/api/recommendations` in the backend).
