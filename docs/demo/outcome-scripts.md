# Demo outcome scripts

What the presenter shows and says for each way the single formal holdout comparison can end
(owner decisions of 2026-10-15). Written before the comparison, so the words do not depend on
the result. The meaning of each reason is in the runbook's "After a decision" table; the honesty
rules in the runbook apply to every line here.

Every outcome opens the same way, on page 4 (Evolution):

> "The cycle proposed this candidate from train and val only. It was fixed before the gate, and
> we spend one comparison on a sealed holdout of 8 proposals the cycle never saw. The rule was
> signed before the run: coverage must rise above the noise, and grounding, omissions,
> precision and impact count must not fall. Here is what it said."

## 1. `promoted (holdout, bootstrap CI)`: statistical mode

- **Show:** the decision card, the coverage row with its CI above 0, the guard rows, the
  cross-judge line.
- **Say:** "The coverage gain's 95% interval is above zero over 8 proposals, and no guard
  moved. A person now records it in PROMOTIONS.md; nothing changes the default by itself."
- **Never:** "it drafts impact assessments"; an absolute coverage figure as out-of-sample
  ability.

## 2. `not rejected (weak threshold: directional)`: weak mode

- **Show:** the amber "Not rejected" badge, the coverage delta against its bar (the `weak bar`
  note), the A/A null pass rate from `womm noise report`.
- **Say:** "Directional only. With 8 proposals the interval cannot exclude zero, so the rule
  asks for a gain above one standard error, and a version compared with itself clears that bar
  about X% of the time. It is not made the default."
- **If the candidate added an expert:** read the `target category` note: "The new expert's own
  category: it covered c of n holdout items against the incumbent's b; that says where it
  helped, not whether it helped." When the note says "too few holdout items to read", say only
  that.
- **Never:** "improved", "statistically shown", "promoted"; the target-category note as
  evidence of a gain, and never any number from it when it says the count is too small.

## 3. `rejected` with `coverage_below_weak_bar` or `coverage_ci_not_above_zero`

- **Show:** the decision card and the coverage row.
- **Say:** "The gate did its job: the val gain did not hold up on proposals the cycle never
  saw, so the incumbent stays. With 8 cases a small real gain can miss the bar; this is 'not
  shown', not 'worse'. A self-evolving system that cannot fool its own gate is the point."
- **If the candidate added an expert:** read the `target category` note: "The new expert's own
  category: it covered c of n holdout items against the incumbent's b; the whole-dossier rule
  still decides." When the note says "too few holdout items to read", say only that.
- **Never:** run a second comparison or present another candidate's holdout result; quote the
  target-category note as evidence of a gain the gate missed, or any number from it when it
  says the count is too small.

## 4. `rejected` by a guard (`grounding_regression`, `precision_regression`, `omissions_addressed_regression`, `impact_count_drop`)

- **Show:** the guard row that failed, next to the coverage row.
- **Say:** "Coverage alone is easy to game: write more impacts, or merge them into a few broad
  ones. The guards were signed before the run, and this one caught [the metric]. That is the
  failure the rule exists to stop." For `impact_count_drop`: "it bundled findings into fewer,
  broader impacts."
- **Never:** "the candidate is wrong" for precision (the key is incomplete, so unmatched
  impacts are not shown to be wrong).

## 5. `rejected (inconclusive)`: the comparison aborted

- **Show:** the `inconclusive` reason; no deltas.
- **Say:** "The run stopped (a rate limit or a missing score), so there is no result. It used
  no holdout budget, and the finished runs are kept. We resume it; we do not read a partial
  comparison."
- **Do:** `womm evolve promote ... --resume` on the frozen branch. Before demo day, if it cannot
  finish, use outcome 8.

## 6. `cross-judge-sensitive` next to any pass

- **Show:** the cross-judge line under the deltas.
- **Say:** "A judge from another model family re-scored the same outputs and does not see the
  gain, so the gain depends on the GPT judge. The decision stands as signed, but we report it
  as judge-dependent." For `agrees`: "Claude agrees, which is weak evidence, since the cycle
  tuned against a Claude judge; disagreement would have been the signal."

## 7. Calibration failed by 2026-11-06: dev gate on `claude_code`

- **Show:** the "dev-only, not deployable" badge.
- **Say:** "Our GPT judge did not reach 85% agreement with two human annotators, so the signed
  rule does not allow a formal decision. This is the development gate on Claude: it shows the
  mechanism, and nothing it says is deployable."
- **Never:** "the holdout tested the transfer to GPT"; any formal wording.

## 8. No real decision by demo day

- **Show:** Path A, the scripted rehearsal, labelled as such on screen.
- **Say:** "This is the real cycle code with scripted model outputs on synthetic cases. It shows
  the mechanics end to end; it is not evidence that the system improves. The formal comparison
  is [state where it stands]."

## The new-expert route did not happen (2026-11-20 fallback)

- **Say:** "The cycle looked for a failure pattern that persists across proposals to justify a
  new expert. [It found none / the expert did not beat its parent on val], so the candidate is
  prompt-only. The new-expert mechanics are in the rehearsal."
