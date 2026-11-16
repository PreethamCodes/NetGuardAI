# ML explained, in plain English

Two short documents, written for a non-CS audience (peers, viva panel, anyone), that explain the
machine-learning half of NetGuard AI without the jargon:

1. **[01-models.md](01-models.md)** — the three jobs we gave the computer, which algorithm we picked
   for each one and why, and what we honestly learned from each.
2. **[02-features.md](02-features.md)** — the 105 numbers ("features") the models are shown every
   second, how they're built, the 12 that matter most, and a real sample of the dataset as a table.

Every number in both documents is taken straight from this project's own results
(`results/ml_results.json` / `results/ml_results.md`) or computed directly from the real trained
models and the real dataset — nothing here is invented for the explanation.

**The one-paragraph version:** every second, we turn raw network measurements into 105 descriptive
numbers, and give those numbers to three separate, simple machine-learning "jobs": *what kind of
traffic is this* (a crowd? an attack? normal?), *is something unusual happening right now*, and *will
the network jam in the next 30 seconds*. For each job we tried a few standard algorithms and kept the
one with the best real-world trade-off — not always the fanciest one.
