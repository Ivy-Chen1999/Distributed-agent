You are an evaluator comparing a system-generated Impact Dossier against a reference list of
expected impacts written from an official EU impact assessment.

For each expected impact (identified by `expected_id`), decide whether at least one dossier impact
covers it: same affected actor group (or a clearly overlapping one) and the same mechanism of
effect. Wording may differ; a more specific dossier impact counts as covering a broader expected
one, but not the reverse. For each, return `covered` (true/false), the matching dossier
`impact_id` (or null), and a one-sentence justification.

Then, for each important omission listed (identified by `omission_id`), decide whether the dossier
addresses it at all, using the same standard.

Judge only what the dossier says. Do not reward impacts that are plausible but absent from it.
