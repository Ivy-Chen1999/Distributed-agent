# Handover: golden cases and the reference check — 2026-10-09

For whoever takes over the golden-case work for WOMM v1 (due around 2026-11-27). The previous
report is `docs/status/2026-10-06-v1-progress.md`; the human steps are in
`docs/status/2026-10-07-v1-human-steps.md`.

## Where it stands

The 11 train/val golden drafts (cases 03–13) are settled by tools alone: three LLM judges accept
what they agree on, and a tie-break judge rules on the rest. Nobody has to review a draft. Before
the drafts are published, two classmates do a **one-time blind reference check** of 20–30 items
(10–15 minutes each, in one sitting) to measure whether that tool chain is robust. That check is
built, reviewed twice and tested; it has not been run with real annotators yet.

## Branches and PRs

| Branch | PR | Contents |
|---|---|---|
| `feat/golden-import` | #5 | Golden import, drafting, tie-break, the grader-model rule and the reference check |
| `feat/v1-integration` | #6 | Integration branch; `feat/golden-import` merged in |
| `feat/eu-cost` | #18 | EU cost work; `feat/golden-import` merged in |
| `feat/provision-comparison` | #3 | Plans and status docs, including this file |
| `review/case-03-…` … `review/case-13-…` | #7–#17 | One draft each, re-settled by the Sonnet tie-break (below) |

PR #7 is case 03, #8 case 04, and so on up to #17 for case 13. If any of these branches is still
ahead of `origin`, it has not been pushed yet: run `git status -sb` on it. The PR descriptions
for #7–#17 must be replaced by the ones generated from the current drafts (the owner keeps them
locally); the descriptions on GitHub may still show the earlier Opus counts.

## Decisions and why

- **A grader never uses the drafter's model.** The drafter and the recall step run on
  claude-opus-5-5. The three judges and the tie-break run on claude-sonnet-5.
  `DraftingRoles` in `src/womm/eval/drafting.py` refuses a config that breaks this. Source:
  Anthropic, *Develop your tests*.
- **The tie-break drops "no effect" claims.** A claim that only says an effect is absent or
  negligible is dropped (`prompts/tools/tiebreak_golden_case.md`). Sonnet kept such claims
  before the rule.
- **Tie-break results per run** (105 open items):

  | Run | Kept |
  |---|---|
  | Opus (not allowed now) | 46 |
  | Sonnet without the rule | 67 |
  | Sonnet with the rule (committed) | 60 |

  The committed run agrees with Opus on 81 of 105 items, and with the earlier Sonnet run on 90.
  Borderline items are unstable even for one model, which is why the human check exists.
- **One-time human check instead of ongoing review.** Anthropic, *Demystifying evals for AI
  agents*, says human review may become occasional once the system is robust. The reference
  check measures that once. Afterwards the `audit: true` spot checks stay optional.

## The reference check in brief

Code: `src/womm/eval/reference_check.py` (the module docstring is the spec),
`src/womm/eval/reference_check_page.html` and `scripts/reference_check.py`. Tests:
`tests/eval/test_reference_check.py`.

1. **Sample** about 20 items, a third each kept by the judges, kept by the tie-break and dropped
   by it. Each annotator gets one self-contained `label_<name>.html`. Nothing on the page reveals
   the tool's decision. A sampled item is never listed among its case's other items, because
   that would reveal whether it was kept.
2. Each annotator **labels alone** and sends back the downloaded `answers_<name>.yaml`.
3. **Score.** An item is settled when more than half of the annotators give the same keep/drop
   label. For the rest, `score` writes `reconcile.html` and the annotators pick one label
   together.
4. **Gate.** The error rate is the higher of the raw rate and the rate reweighted to the
   population's strata. An unsettled item counts as an error, and every annotator must answer.
   The rate must be at most 10%. The output also shows agreement before reconciling (raw and
   Cohen's kappa) and a one-sided 95% upper bound of the error rate.

Every file carries the sample id; files from another sample are refused. `key.private.json`
holds the tool's decisions and is never sent to an annotator.

## Next steps, in order

1. Push the branches above. The owner runs every push; nothing is force-pushed. Then replace
   the descriptions of #7–#17.
2. Merge the review PRs into `feat/golden-import`, or carry their drafts over. Then draw the
   real sample from the merged drafts, with the classmates' names:

   ```
   uv run python scripts/reference_check.py sample evals/golden/drafts/case_*.yaml \
       --annotator <name1> --annotator <name2> --send-to "<owner> on <channel>" \
       --out .cache/reference_check/round1/ --n 30
   ```

   Draw the sample **before** publishing: `scripts/publish_golden_cases.py` deletes each draft
   it publishes.
3. Collect the answers, run `score`, reconcile if asked, and score again. Record the result in
   the human-steps file (step 1a).
4. If it is ROBUST, publish. If not, fix the drafting or tie-break prompt, re-run the
   tie-break, and check again with a new `--out` and a new `--seed`.
5. Then continue with the human-steps file: the eval run, `womm calibrate sample`, the labelling
   page for the judge calibration (U6), and the owner reading 3–4 eval transcripts.

## Open decisions

- **Sample size.** No error in 20 items still allows about 12% at 95%, so 20 items rule out only
  gross failures. No error in 30 items shows at most 10%, at about 5 more minutes per person.
  Recommended: `--n 30`.
- **Cross-family grader.** The judges are another model of the same family. A grader from
  another provider would be stronger, but it waits on the decision about an OpenAI key or API.
- **Case 07 is thin.** The tie-break dropped 10 of its 11 open items, leaving 5 expected impacts.
  It passes the strict gate. Look at it if the check flags tie-break drops.

## Gotchas

- Never commit holdout identifiers, API keys or client names; the repository is public. Keys
  live only in the local `.env`.
- LLM roles use the `claude_code` backend (Claude Code subscription, no API cost); respect the
  budget caps in `evals/drafting.yaml`.
- CI is strict on golden drafts unless the PR is a draft (`WOMM_DRAFT_PR`). Marking a review PR
  ready runs the strict gate.
- In zsh, `$var:evals/...` applies a filename modifier; write `${var}:evals/...` in loops over
  git refs.
- A local hook blocks `rm -rf`; delete specific files instead.
