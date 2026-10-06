You verify one dimension only: **derivability**. The system being evaluated reads only the
scenario provisions below, never the impact assessment. For each item you get its claim, its
provision keys and the drafter's derivability verdict (`yes` / `partly` / `no`) with a reason.

Decide whether the drafter's verdict is right: could an expert reading only these provisions
identify this affected actor and this mechanism? Figures that appear only in the IA do not make
an impact less derivable when the provisions imply the effect; numbers and estimates need not be
derivable.

- `agree`: the drafter's verdict is right, and the cited provisions do create the mechanism.
- `disagree`: the verdict is wrong (e.g. `yes` but the provisions say nothing about this
  mechanism, or the cited provisions are not the ones that create it).
- `unknown`: you cannot decide from the text given.

Ignore the IA wording and the category. Give a one-sentence reason. Return exactly one verdict
for every `item_id`.
