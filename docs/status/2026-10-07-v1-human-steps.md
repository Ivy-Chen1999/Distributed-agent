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
| 2 | Reference check (**live since 2026-10-11**, samuel and eetu) | 2 classmates | about 15 min each |
| 3 | Read the reference-check review page, mark who is right | owner | about 5 min |
| 3b | RSB companions: commit them to the review branches, then the classmates label all of them | owner, then 2 classmates | 10 min, then about 15 min each |
| 4 | Publish the golden cases; then the calibration sitting | owner, then 2 classmates | 5 min, then about 20 min each |
| 5 | Holdout: spot-check 3 items a draft on a local page (tie-break **done 2026-10-12**), import | owner | about 20 min |
| 6 | Sign the promotion policy (**done 2026-10-12**) | owner | 5 min |
| 7 | R34 noise run and MDD report | owner starts it | 10 min plus run time |
| 8 | Cost reference: run the check, record the FTE basis (**done 2026-10-12**) | owner | 5 min |

Order: 0, 1, 2, 3, 4; the calibration in 4 needs the published cases and the API key (the gate
calibrates the API grader, and the published cases give it 13 cases instead of 2); 5 needs a
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

## 2. The reference check (2 classmates, about 15 minutes each)

**Done 2026-10-11:** drawn from the 11 drafts as they are on the `review/case-*` branches
(copied to `.cache/reference_check/round1_drafts/`), seed 2 (the practice sample used seed 1),
20 items each and a reserve of 10 each, and uploaded. Link to send to samuel and eetu:
`https://womm-api-production.up.railway.app/label/2026-10-11-86dae710`. Do not send them the
practice links. If a draft changes before publishing, draw a new check. How it was made:

```
uv run python scripts/reference_check.py sample .cache/reference_check/round1_drafts/case_*.yaml \
    --annotator samuel --annotator eetu --n 20 --extra 10 --seed 2 --out .cache/reference_check/round1/
uv run python scripts/reference_check.py upload --dir .cache/reference_check/round1/ --url https://<app>
```

`upload` prints the link. `--annotator` is each
classmate's name (letters, digits, `-`, `_`); they sign in by typing it (case does not matter). Draw the reference-check sample before
publishing (step 4 deletes the drafts).

**Classmates:** open the link, sign in with their name, read the welcome card and the short
guide on the first item, answer every item, press "Finish & send". Answers are saved as they
go; they can stop and come back. Work alone, and don't discuss items until both have sent.
Progress is visible at `https://<app>/label/<sample>/admin` (asks for the admin token).

**If the reference check finds an error in the first round,** `score` says NOT YET. Then run
`uv run python scripts/reference_check.py extend --dir .cache/reference_check/round1/ --url https://<app>`
and ask both to open the reference-check link again: 10 more items each, about 5 minutes.

## 3. Read the review page (owner, about 5 minutes)

When the admin page shows both as sent:

```
uv run python scripts/reference_check.py fetch --dir .cache/reference_check/round1/ --url https://<app>
uv run python scripts/reference_check.py score --dir .cache/reference_check/round1/
```

`score` writes `review.html` in its directory: the verdict in plain words with what to do
next, and every item where the people and the AI disagree, side by side with the AI's own
reason. Open it and mark who is right on each disagreement; the advice on the page updates.
This replaces reading eval transcripts by hand. The pages hold the AI's decisions: keep them
local, never send them. Record the reference check here:

| Date | Sample id | Error | 95% bound | Verdict |
|---|---|---|---|---|
| | | | | |

## 3b. RSB companions (after a ROBUST reference check)

The RSB opinions were fetched on 2026-10-12 (15 of 23 laws) and a companion draft of RSB
omissions was drafted for each of the 11 practice cases (31 points, 18 kept by the AI checks;
`docs/status/2026-10-12-rsb-companions.md`). They are drafted locally and not committed until
the reference check passes. Then, on the integration branch:

```
# merge the companion code into each review branch, then commit its companion
for b in $(git branch --list 'review/case-*' --format='%(refname:short)'); do
  git switch $b && git merge --no-edit feat/v1-integration && git switch -
done
# on each branch: copy the companion drafted on 2026-10-12 (kept in .cache/rsb_companions/),
# check it, commit it (no new LLM call; same drafts, same result as the status report)
cp .cache/rsb_companions/<case>.rsb.yaml evals/golden/drafts/
uv run python scripts/review_draft.py evals/golden/drafts/<case>.rsb.yaml --check
# the census: every companion point, split between the two classmates
uv run python scripts/reference_check.py sample evals/golden/drafts/case_*.yaml \
    --annotator samuel --annotator eetu --companions --overlap 3 --out .cache/reference_check/companions/
uv run python scripts/reference_check.py upload --dir .cache/reference_check/companions/ --url https://<app>
```

`--overlap 3` gives each classmate 3 of the other's points as well (about 2 minutes more): the
score then says how often the two agree with each other, so a disagreement with the AI can be
weighed. Those copies never count in the 10%. Score it like step 3. At most 10% wrong: the companions are published with their cases. More:
delete the companion files, fix the input, draft `--version rsb2` and check again.

- Reference check ROBUST: go on to step 4. NOT ROBUST: fix the prompt the review page points
  at, settle the drafts again, and run a new check (new `--out`, new `--seed`).

## 4. Publish the golden cases, then the calibration sitting

```
uv run python scripts/oracle_check.py evals/golden/drafts/case_*.yaml   # each case's own answer must score full
uv run python scripts/publish_golden_cases.py
```

Commit the published cases. Then the calibration (needs the OpenAI key in `.env`): one train
eval run with the API incumbent, 30 pairs that both classmates label, about 20 minutes each.

```
uv run womm eval --system-version system_versions/v0.3-api.yaml --split train --local
uv run womm calibrate sample --sv system_versions/v0.3-api.yaml --report runs/<that report> \
    --n 30 --seed 1 --annotator samuel --annotator eetu --out .cache/calibration/round1/
uv run womm calibrate upload --dir .cache/calibration/round1/ --url https://<app>
# when both have sent:
uv run womm calibrate fetch --dir .cache/calibration/round1/ --url https://<app>
uv run womm calibrate score --dir .cache/calibration/round1/ --record
git commit evals/promotion_records.yaml
```

- Calibration counts only when the two classmates agree with each other on at least 80% of the
  pairs and at least 24 of the 30 have a clear answer; then the grader must agree with them on
  at least 85%. Passed: recorded, and the statistical and weak gates can run. People below 80%:
  the rule is unclear; sharpen its wording from the split pairs on the review page, then a new
  seed. Grader below 85%: fix the coverage judge's prompt, then a new seed.

## 5. Holdout (owner only, about 20 minutes)

The holdout never leaves the owner's machine, so this cannot go to classmates. It needs a
ROBUST reference check (step 3): the same tool chain then settles the holdout drafts, and the
owner only checks 3 items a draft (one per kind of decision), about 24 in all.

```
uv run python scripts/tiebreak_golden_case.py .cache/drafts/<case>.yaml      # each draft
uv run python scripts/holdout_review.py --reference-check .cache/reference_check/round1/ \
    --reviewer "Chen Yuxin"     # opens a local page with every draft's 3 items, blind
HOLDOUT_DATABASE_URL=... uv run python scripts/import_holdout_case.py .cache/holdout_import/<case>.yaml
```

The page runs on this machine only (127.0.0.1), never on Railway. The browser's translate button
works on it; it sends the page text to the translation service, so use it only if that is
acceptable for the holdout.

Any disagreement with the tool on a draft refuses the spot check (the same rule as the
reference check's early stop; the holdout is newer material than train/val); then run
`holdout_review.py ... --full <case_id>` for that draft (every item, same page). Done: `.cache/drafts/` is empty
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
- **R37 reference answers** (`evals/diff_regression/demo_penalties_amended.yaml`): LLM-drafted
  2026-10-12 on `feat/eu-cost` (monitoring only, gate nothing); every quote is machine-checked
  against the article texts.
- **Holdout tie-break:** done 2026-10-12. The 8 drafts in the integration worktree's
  `.cache/drafts/` are settled (75 items ruled, none left for a person); only the spot check of
  24 items remains, after a ROBUST reference check.
