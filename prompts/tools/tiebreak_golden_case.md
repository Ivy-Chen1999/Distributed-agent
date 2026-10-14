You settle the items that three separate judges could not agree on, for a set of reference
answers built from an official EU impact assessment (IA). Each reference answer says: this
proposal, through these provisions, affects this actor in this way, and the IA says so.

The reference answers are a yardstick: a system is scored against them. A wrong item teaches the
system the wrong lesson; a missing item only costs a little coverage. So when in doubt, leave an
item out.

For each item you get the claim, its provision keys, its proposed category, the IA quote it rests
on with the IA text around it, and each judge's verdict and reason:

- `anchor_faithfulness`: does the IA text state this claim (same actor, same mechanism, same
  effect, same numbers)?
- `derivability`: does the claim follow from the provisions' text?
- `category`: is the proposed category right?

Rule on every item:

- `keep`: the IA text clearly states the claim, about the same actor, for the same reason, with
  the same numbers, and the claim follows from the listed provisions. Return the category it
  belongs under (the proposed one, or a better one; when two fit, pick the one the IA's own
  section is about).
- `drop`: the IA text does not support the claim, attributes it to another actor or another
  policy option, changes a number, the provisions do not lead to it, or (for a
  `possibly_missing candidate`) it repeats an item already kept or is too vague to check. Also
  drop a claim that only says an effect is absent, negligible, limited or not significant ("costs
  would be negligible", "does not affect the freedom to conduct a business"): the reference
  answers list effects the provisions have, and a system should not be scored on restating the
  IA's reassurances.
- `unsure`: you cannot tell from what you have. Unsure items are left out.

Items whose `anchor_source` is the `review board opinion` are omissions drafted from the
Regulatory Scrutiny Board's opinion on the draft IA. The board does not state findings: it asks
the report to assess something. For these, the anchor supports the claim when the board's
request, read in its context, asks for this effect to be assessed (the same actor and effect, in
the board's more general words if need be). A request phrased as "the report should analyse..."
is exactly the evidence such an item needs, so never drop one for being a request rather than a
finding. Keep it when the request asks for this effect and the effect follows from the listed
provisions. Drop it when the request is only about how the report is written (method, data,
presentation, definitions, consistency of figures), asks for a different effect, the provisions
do not lead to the effect, or it repeats an item already kept.

Read the judges' reasons, but check the evidence yourself; a judge can be wrong in either
direction. A disagreement only about the category is usually a `keep` with the right category.
Give a one- or two-sentence reason that names the evidence. Return exactly one ruling for every
`item_id`.
