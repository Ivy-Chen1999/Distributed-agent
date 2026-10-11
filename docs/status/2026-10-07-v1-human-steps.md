# WOMM v1: the steps that need a person — 2026-10-07 (revised 2026-10-10)

Everything else in v1 is automated. People are needed in two places only, because there an
LLM is being checked and another LLM cannot be the reference: is the AI-made answer key right
(the reference check), and does the AI grader judge like people do (the calibration). Both are
one classmate sitting on the deployed app. The rest of this page is the owner's: permissions,
one signature, and commands that run unattended. Commands refer to `feat/v1-integration`.

Revised 2026-10-10 to the least human work: the holdout is settled by the same tool chain as
train/val and only spot-checked; transcript reading is replaced by the review pages that
`score` writes; the 11-20 route is stated by `womm evolve cycle`; the IA cost reference is
checked by a script; the hand-written R37 answers move to v1.1 (monitoring only, they gate
nothing).

| # | Step | Who | Time |
|---|---|---|---|
| 0 | One-off setup: push, deploy, tokens, API key, branch protection | owner | about 30 min |
| 1 | Merge the 11 train/val drafts | owner | 5 min |
| 2 | Classmates' sitting: reference check and grader calibration | 2 classmates | about 40 min each |
| 3 | Read the two review pages, mark who is right | owner | about 10 min |
| 4 | Publish the golden cases | owner | 5 min |
| 5 | Holdout: tie-break, spot-check 3 items a draft, import | owner | about 20 min |
| 6 | Sign the promotion policy | owner | 5 min |
| 7 | R34 noise run and MDD report | owner starts it | 10 min plus run time |
| 8 | Cost reference: run the check, record the FTE basis | owner | 5 min |

Order: 0, 1, then 2 (it needs the merged drafts and one train eval run), 3 and 4; 5 needs a
passed reference check; 6 before any holdout comparison; 7 and 8 any time after 0.

---

## 0. One-off setup (owner)

**Done 2026-10-10, updated 2026-10-11:** `feat/v1-integration` (e79413f) is deployed to the Railway service
`womm-api` with `railway up` from a clean `git archive` (no git push, `main` unchanged), and
`WOMM_LABEL_ADMIN_TOKEN` is set on Railway and in the local `.env`. A production smoke run
(sample, upload, sign-in, label, submit, fetch, score) passed. Two practice samples are up
(annotator names `classmate1`, `classmate2`): reference check `/label/2026-10-10-2ecc6be6`,
calibration `/label/2026-10-10-50346b5f`; admin pages at `/label/<sample>/admin`. **Caveat:** a
push to `main` redeploys `main`, which has no labelling pages yet: merge `feat/v1-integration`
into `main` first (the pages and saved answers then stay). Still to do below: push, API key,
branch protection.

- Push every branch (normal push). Merge `feat/v1-integration` into `main`; Railway deploys
  `main`.
- Railway service variables: `WOMM_LABEL_ADMIN_TOKEN`, at least 16 random characters. Put the
  same value in the local `.env`. Check that `https://<app>/label/x` answers 404 (not the
  console).
- API key, for step 7 and formal gates: set a monthly spend limit in the provider console first,
  then put the key in the local `.env` only.
- GitHub, Settings, Branches, `main`: require a pull request and "Require review from Code
  Owners".

## 1. Merge the 11 train/val drafts (owner)

The drafts are settled by tools (three judges and a tie-break); nobody reviews them.

```
for n in $(seq 7 17); do gh pr ready $n && gh pr merge $n --merge; done
git switch feat/v1-integration && git merge feat/golden-import
```

## 2. The classmates' sitting (2 classmates, about 40 minutes each)

**Owner, before** (about 10 minutes of commands; the eval run takes longer but runs alone):

```
# the reference check: 20 items each, a reserve of 10 each held back
uv run python scripts/reference_check.py sample evals/golden/drafts/case_*.yaml \
    --annotator <name1> --annotator <name2> --out .cache/reference_check/round1/
uv run python scripts/reference_check.py upload --dir .cache/reference_check/round1/ --url https://<app>

# the grader calibration: one train eval run, then 30 pairs that both label
uv run womm eval --split train --local
uv run womm calibrate sample --sv system_versions/<incumbent>.yaml --report runs/<that report> \
    --n 30 --seed 1 --annotator <name1> --annotator <name2> --out .cache/calibration/round1/
uv run womm calibrate upload --dir .cache/calibration/round1/ --url https://<app>
```

Each `upload` prints one link. Send both links to both classmates. `--annotator` is each
classmate's name (letters, digits, `-`, `_`); they sign in by typing it (case does not matter). Draw the reference-check sample before
publishing (step 4 deletes the drafts).

**Classmates:** open each link, sign in with their name, read the welcome card and the short
guide on the first item, answer every item, press "Finish & send". Answers are saved as they
go; they can stop and come back. Work alone, and don't discuss items until both have sent.
Progress is visible at `https://<app>/label/<sample>/admin` (asks for the admin token).

**If the reference check finds an error in the first round,** `score` says NOT YET. Then run
`uv run python scripts/reference_check.py extend --dir .cache/reference_check/round1/ --url https://<app>`
and ask both to open the reference-check link again: 10 more items each, about 5 minutes.

## 3. Read the review pages (owner, about 10 minutes)

When the admin pages show both as sent:

```
uv run python scripts/reference_check.py fetch --dir .cache/reference_check/round1/ --url https://<app>
uv run python scripts/reference_check.py score --dir .cache/reference_check/round1/
uv run womm calibrate fetch --dir .cache/calibration/round1/ --url https://<app>
uv run womm calibrate score --dir .cache/calibration/round1/ --record
git commit evals/promotion_records.yaml
```

Each `score` writes `review.html` in its directory: the verdict in plain words with what to do
next, and every item where the people and the AI disagree, side by side with the AI's own
reason. Open both and mark who is right on each disagreement; the advice on the page updates.
This replaces reading eval transcripts by hand. The pages hold the AI's decisions: keep them
local, never send them. Record the reference check here:

| Date | Sample id | Error | 95% bound | Verdict |
|---|---|---|---|---|
| | | | | |

- Reference check ROBUST: go on to step 4. NOT ROBUST: fix the prompt the review page points
  at, settle the drafts again, and run a new check (new `--out`, new `--seed`).
- Calibration counts only when the two classmates agree with each other on at least 80% of the
  pairs and at least 24 of the 30 have a clear answer; then the grader must agree with them on
  at least 85%. Passed: recorded, and the statistical and weak gates can run. People below 80%:
  the rule is unclear; sharpen its wording from the split pairs on the review page, then a new
  seed. Grader below 85%: fix the coverage judge's prompt, then a new seed.

## 4. Publish the golden cases (owner)

```
uv run python scripts/oracle_check.py evals/golden/drafts/case_*.yaml   # each case's own answer must score full
uv run python scripts/publish_golden_cases.py
```

Then commit the published cases.

## 5. Holdout (owner only, about 20 minutes)

The holdout never leaves the owner's machine, so this cannot go to classmates. It needs a
ROBUST reference check (step 3): the same tool chain then settles the holdout drafts, and the
owner only checks 3 items a draft (one per kind of decision), about 24 in all.

```
uv run python scripts/tiebreak_golden_case.py .cache/drafts/<case>.yaml
uv run python scripts/verify_golden_case.py --draft .cache/drafts/<case>.yaml \
    --spot-check --reference-check .cache/reference_check/round1/
HOLDOUT_DATABASE_URL=... uv run python scripts/import_holdout_case.py .cache/holdout_import/<case>.yaml
```

Any disagreement with the tool on a draft refuses the spot check (the same rule as the
reference check's early stop; the holdout is newer material than train/val); then run
`verify_golden_case.py` without `--spot-check` for that draft. Done: `.cache/drafts/` is empty
and the holdout store reports 8 cases from 8 proposals.

## 6. Sign the promotion policy (owner)

`evals/promotion_policy.yaml` carries the plan's defaults (coverage primary; grounding may drop
at most 0.02; omissions at most one noise SD; `expected_gain` 0.08; dev-mode comparisons count
against the budget of 6). Change a value first if you disagree, then:

```
uv run womm policy sign --by "<your name>"
git commit evals/promotion_policy.yaml
```

Never change the file after a comparison; every decision records its sha256.

## 7. R34 noise run and MDD (owner starts it)

```
uv run womm eval --split val --repetitions 6 --formal
uv run womm noise report --report <that report> --holdout-cases 8 --holdout-proposals 8 --record
git commit evals/promotion_records.yaml
```

If the MDD is above `expected_gain`, the gate uses the weak threshold, and the demo says so.

## 8. Cost reference (owner, on `feat/eu-cost`)

```
uv run python scripts/check_ia_cost_reference.py
uv run python scripts/check_ia_cost_reference.py --write --fte-basis section_6_2 --by "<your name>"
```

The script checks every transcribed figure against the IA text (11 of 11 matched on
2026-10-10). The one choice is which EU-level FTE figure to use: 10 FTE (IA section 6.2) or 5
FTE (Annex 3, Table 5); pass `annex3_table5` for the second. Then commit the file, and add
`v1.0-cost` and its api twin to `system_versions/PROMOTIONS.md`.

## Not a human step any more

- **The 2026-11-20 route:** `womm evolve cycle` prints `route: new-expert` or `route:
  prompt-only` with the reason, applying the runbook's rule.
- **R37 reference answers** (`evals/diff_regression/demo_penalties_amended.yaml`): v1.1. They
  are monitoring only and gate nothing; when written, still by hand, never LLM-drafted.
