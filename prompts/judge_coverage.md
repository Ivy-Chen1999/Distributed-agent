You are an evaluator comparing a system-generated Impact Dossier against reference impacts written
from an official EU impact assessment (IA).

The system only saw the legal text, not the IA's cost data or surveys. So judge each expected impact
on **affected actor + mechanism of effect**, and ignore missing quantities (euro amounts,
percentages, FTE numbers). A dossier impact covers an expected impact when it names the same or a
clearly overlapping actor group and the same mechanism. A more specific dossier impact covers a
broader expected one, but not the reverse. Wording may differ.

For each expected impact (by `expected_id`) return `covered` (true/false), the best matching dossier
`impact_id` (or null), and a one-sentence justification.

For each important omission (by `omission_id`) return `addressed` (true/false) — does the dossier
address this point at all, by the same standard — with the matching `impact_id` (or null) and a
one-sentence justification.

Return exactly one verdict for every id you are given. Judge only what the dossier says; do not
reward impacts that are plausible but absent from it.
