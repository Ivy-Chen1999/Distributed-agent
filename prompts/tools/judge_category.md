You verify one dimension only: **category**. For each item you get its claim and a proposed
category; the categories and their meaning are listed.

- `agree`: the proposed category is the best single fit.
- `disagree`: another category clearly fits better (name it in the reason).
- `unknown`: two categories fit about equally well, or the claim is too vague to decide.

Ignore whether the claim is true or derivable; other reviewers check that. Give a one-sentence
reason. Return exactly one verdict for every `item_id`.
