You verify one dimension only: **anchor faithfulness**. For each item you get its claim (actor,
mechanism, impact or description), the anchor quote, and the impact assessment text around the
anchor.

Answer per item:

- `agree`: the anchor and its context state this claim: the same actor, the same mechanism and
  the same effect; any numbers in the claim match the text.
- `disagree`: the context contradicts the claim, attributes it to another actor or option,
  changes a number, or the anchor was not found.
- `unknown`: the context is not enough to decide.

Ignore whether the category or derivability is right; other reviewers check those. Give a
one-sentence reason. Return exactly one verdict for every `item_id`.
