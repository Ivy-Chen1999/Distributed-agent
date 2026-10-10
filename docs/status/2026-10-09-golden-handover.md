# Handover: golden cases and the reference check — 2026-10-09

For whoever takes over the golden-case work for WOMM v1 (due around 2026-11-27). The previous
report is `docs/status/2026-10-06-v1-progress.md`; the human steps are in
`docs/status/2026-10-07-v1-human-steps.md`.

## Terms

- **IA:** the European Commission's impact assessment of a proposal. Golden cases are built from
  it. **RSB:** the Regulatory Scrutiny Board, whose opinion on the IA is a second source.
- **Golden case:** the reference answers for one proposal: which actors the provisions affect, how,
  and what the IA leaves out. The system is scored against them. Cases 03–13 are the train/val
  split; the holdout cases are separate and never shared.
- **Draft:** a golden case before publishing, in `evals/golden/drafts/`. An LLM drafter writes it
  from the IA; three LLM judges check every item.
- **Open item:** an item the three judges did not all accept. **Tie-break:** a fourth LLM call
  that rules keep, drop or unsure on each open item.
- **Stratum:** how an item was decided: kept by the judges, kept by the tie-break, or dropped by
  it. The reference check samples a third of each.
- **Strict gate:** the CI check on drafts (`tests/eval/test_golden_drafts.py`); a draft PR is
  exempt until it is marked ready.
- **U5, U6 …:** implementation units of `docs/plans/2026-10-04-001-feat-golden-case-expansion-plan.md`
  and the plans after it.

## Where it stands

The 11 train/val drafts (cases 03–13) are settled by tools alone, so nobody has to review a draft.
Before the drafts are published, two classmates do a **one-time blind reference check**: 20
items each, about 15 minutes, each on their own items. It measures whether the tool chain is
robust. It runs on the deployed app: one link, sign-in by name, answers saved as people label.
The check is built and tested end to end; it has not been run with real annotators yet.

## Branches and PRs

| Branch | PR | Contents |
|---|---|---|
| `feat/golden-import` | #5 | Golden import, drafting, tie-break, the grader-model rule and the reference check |
| `feat/v1-integration` | #6 | Integration branch; `feat/golden-import` merged in; has `womm calibrate` and the hosted labelling pages (`womm.api.labels`) |
| `feat/eu-cost` | #18 | EU cost work; `feat/golden-import` merged in |
| `feat/provision-comparison` | #3 | Plans and status docs, including this file |
| `review/case-03-…` … `review/case-13-…` | #7–#17 | One draft each, re-settled by the Sonnet tie-break (below) |

PR #7 is case 03, #8 case 04, and so on up to #17 for case 13. The review PRs target
`feat/golden-import`. **The drafts exist only on the review branches** until those PRs are
merged; `evals/golden/drafts/` on `feat/golden-import` is empty.

## Before handing over (the current owner)

1. Push every branch above (normal push, no force). Until then a fresh clone shows none of this
   work, including this file. Check with `git status -sb` on each branch.
2. Replace the descriptions of #7–#17 with the ones generated from the current drafts. They are
   on the owner's machine in `.cache/handover/pr_bodies/pr_<N>_body.md` (gitignored); the
   descriptions on GitHub still show the earlier Opus counts.
3. Deploy the labelling pages: merge `feat/v1-integration` into `main` (Railway deploys
   `main`) and set `WOMM_LABEL_ADMIN_TOKEN` (at least 16 random characters) in the Railway
   variables and in the local `.env`. Without it the `/label` pages do not exist.
4. Draw the reference-check sample yourself (step 2 below) or hand over the IA cache: the
   labelling pages show the text around each IA quote only when `.cache/ia/<fixture>/` exists.
   It is filled by `scripts/draft_golden_case.py` and is not in git; without it the pages show
   the quote alone.

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
  check measures that once. Afterwards the `audit: true` spot checks (20% of kept items, marked
  in each draft) stay optional.
- **20 items per person, different items, in two looks.** Two people on different items measure
  the tool better than two on the same items, and need no joint session. The check stops after
  the first 40 only if there is no error at all (the 95% bound is then about 6%, under the 10%
  limit); any error sends a reserve of 10 more each, drawn at the same time (`extend`), and the
  10% rule applies to all 60. `--n` (first-round items per annotator) defaults to 20, `--extra`
  to 10.

## The reference check in brief

Code: `src/womm/eval/reference_check.py` (the module docstring is the spec),
`src/womm/eval/labelling_page.html` (shared with the calibration), `scripts/reference_check.py`,
and the hosting in `src/womm/api/labels.py` and `src/womm/eval/hosted_labels.py`. Tests:
`tests/eval/test_reference_check.py`, `tests/api/test_labels.py` and
`tests/api/test_labels_pipeline.py` (the whole path against Postgres). Edit this code on
`feat/v1-integration`: the hosting exists only there.

1. **Sample** 20 items per annotator (plus a held-back reserve of 10), a third from each stratum, each annotator their own items,
   and **upload** them to the app. Nothing on the page reveals the tool's decision. A sampled item
   is never listed among its case's other items, because that would reveal whether it was kept.
2. Each annotator opens the link, signs in with their name and **labels** (keep, drop or can't
   tell, with an optional note); every answer is saved on the app. The owner **fetches** them
   back as `answers_<name>.yaml`. (A self-contained `label_<name>.html` per annotator is still
   written, as a fallback without the app; it downloads the answer file instead.)
3. **Score.** `score` also writes `review.html` (local only): the verdict in plain words and
   each disagreement next to the tool's own reason. The tool's error rate is the share of items where the person's keep/drop differs
   from the tool; "not sure" counts as an error. ROBUST at 10% or less, with every item
   answered. The output shows the rate per stratum and a one-sided 95% upper bound, and for each
   wrong item the tool's own reason and the person's note.

Every file carries the sample id; files from another sample are refused. `key.private.json`
holds the tool's decisions and is never sent to an annotator or uploaded; the app refuses cards
that carry a decision.

**To practise,** run the app locally (`README.md`, with `WOMM_LABEL_ADMIN_TOKEN` set), draw a
sample with made-up names into a scratch directory (`--annotator demo1 --annotator demo2 --out
.cache/reference_check/practice/`), `upload` it to `http://localhost:8000`, label a few items as
each name, then `fetch` and `score`.

## Next steps, in order

1. Mark the review PRs #7–#17 ready (the strict gate passes on each) and merge them into
   `feat/golden-import`. The drafts are then in `evals/golden/drafts/`.
2. Merge `feat/golden-import` into `feat/v1-integration` (the `upload` and `fetch` commands
   exist only there) and, on `feat/v1-integration`, draw the real sample with the classmates'
   names (letters, digits, `-` and `_` only; they type it to sign in):

   ```
   uv run python scripts/reference_check.py sample evals/golden/drafts/case_*.yaml \
       --annotator <name1> --annotator <name2> --send-to "<owner> on <channel>" \
       --out .cache/reference_check/round1/
   ```

   Draw the sample **before** publishing: `scripts/publish_golden_cases.py` deletes each draft
   it publishes. Then `uv run python scripts/reference_check.py upload --dir
   .cache/reference_check/round1/ --url https://<app>` and send the printed link to both.
3. When `https://<app>/label/<sample>/admin` shows both as sent, run `fetch` (same arguments as
   `upload`), then `uv run python scripts/reference_check.py score --dir
   .cache/reference_check/round1/`, and record the result in the table in step 1a of the
   human-steps file.
4. Run the full-score check on the drafts (no people; one coverage-judge call per case):
   `uv run python scripts/oracle_check.py evals/golden/drafts/case_*.yaml`. It scores each
   case's own reference answer, which must reach full coverage; an item it fails points at a
   broken item or the judge, not at the system. Fix those first. Without paths it checks the
   published cases.
5. If both checks pass, run `scripts/publish_golden_cases.py` and commit the published cases. If
   not, fix the drafting or tie-break prompt and settle the open items again. The tie-break
   refuses a draft it has already settled, so first restore the draft as it was before: find
   the first draft commit with `git log --oneline -- evals/golden/drafts/<case>.yaml` and
   `git show <commit>:evals/golden/drafts/<case>.yaml > evals/golden/drafts/<case>.yaml`, then
   run `uv run python scripts/tiebreak_golden_case.py evals/golden/drafts/<case>.yaml`. Check
   again with a new `--out` and a new `--seed`.
6. Then continue with the human-steps file on `feat/v1-integration` (merge `feat/golden-import`
   into it first; `womm calibrate` exists only there): the train/val eval run, the judge
   calibration (step 3: `womm calibrate sample`, then `upload`, `fetch` and `score --record`, on
   the same labelling pages; its review page replaces reading eval transcripts by hand). The
   holdout then follows step 5 of the human-steps file: tie-break plus a 3-item spot check per
   draft. Ask the classmates for
   both tasks in one sitting.

## Open decisions

- **Cross-family grader.** The judges are another model of the same family. A grader from
  another provider would be stronger, but it waits on the decision about an OpenAI key or API.
- **Case 07 is thin.** The tie-break dropped 10 of its 11 open items, leaving 5 expected impacts.
  It passes the strict gate. Look at it if the check flags tie-break drops.

## Gotchas

- Never commit holdout identifiers, API keys or client names; the repository is public. Keys
  live only in the local `.env`.
- LLM roles use the `claude_code` backend (the Claude Code subscription, no API cost).
  `evals/drafting.yaml` sets each role's model, prompt and timeout, and
  `max_parallel_llm_calls`.
- CI is strict on golden drafts unless the PR is a draft (`WOMM_DRAFT_PR`). Marking a review PR
  ready runs the strict gate.
- In zsh, an unmatched glob such as `evals/golden/drafts/case_*.yaml` is an error, not an empty
  list; and `$var:evals/...` applies a filename modifier, so write `${var}:evals/...` in loops
  over git refs.
- A local hook blocks `rm -rf`; delete specific files instead.
