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
  `possibly_missing candidate`) it repeats an item already kept or is too vague to check.
- `unsure`: you cannot tell from what you have. Unsure items are left out.

Read the judges' reasons, but check the evidence yourself; a judge can be wrong in either
direction. A disagreement only about the category is usually a `keep` with the right category.
Give a one- or two-sentence reason that names the evidence. Return exactly one ruling for every
`item_id`.
