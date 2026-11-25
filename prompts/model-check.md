# Monthly model review

Inputs: the approved writer and reviewer pair, their runtime configuration, and
the next review date. Follow the [model policy](../rules/MODELS.md).

1. Read the approved pair, last decision, evaluation tasks, and current runtime
   routes. Keep writing and review with different model providers.
2. Check official provider release notes and documentation for availability,
   supported capabilities, pricing, and quota changes. Record publication dates
   and direct source links. Distinguish newly announced features from features
   available to the configured account and tools.
3. Compare promising candidates with the current pair using the same writing
   tasks, acceptance checks, and known-defect review cases. Record accuracy,
   missed defects, rework, latency, and cost. Do not infer quality from the model
   name alone. Run the comparison even when there are no new releases.
4. Prepare one monthly decision record: current configuration, verified changes,
   evidence from the comparison, recommendation, and consequences. Reuse an
   existing record for the same month rather than creating duplicates.
5. Keep the approved models until the maintainer explicitly authorizes a change.
   Silence does not authorize a model switch. After the decision, update the
   pairing record and runtime configuration through a reviewed PR, then record
   the next monthly review date.

Do not expose credentials, account identifiers, or provider responses containing
private data. Report an unavailable source or evaluation as unavailable.
