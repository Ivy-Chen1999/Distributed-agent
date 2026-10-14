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
| 0 | One-off setup: push, deploy, tokens, API key (**key done 2026-10-14**), branch protection | owner | about 30 min |
| 1 | Merge the 11 train/val drafts | owner | 5 min |
| 2 | Reference check with the disputed expert points (**live since 2026-10-14**, samuel and eetu) | 2 classmates | about 20 min each |
| 3 | Read the reference-check review page, mark who is right | owner | about 5 min |
| 3b | RSB companions: commit them to the review branches (labelled in step 2) | owner | 10 min |
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
into `main` first (the pages and saved answers then stay). Still to do below: push and branch
protection; the API key is done (2026-10-14, below).

- Push every branch (normal push). Merge `feat/v1-integration` into `main`; Railway deploys
  `main`.
- Railway service variables: `WOMM_LABEL_ADMIN_TOKEN`, at least 16 random characters. Put the
  same value in the local `.env`. Check that `https://<app>/label/x` answers 404 (not the
  console).
- API key, for step 7 and formal gates: set a monthly spend limit in the provider console first,
  then put the key in the local `.env` only. **Done 2026-10-14:** the OpenAI key is in the local
  `.env`. The api versions are pinned (on `feat/v1-integration`, `system_versions/PROMOTIONS.md`):
  the Planner on `gpt-6-luna`, every other role on `gpt-6.1-sol`, reasoning effort `medium`.
  A live run of `case_02` on `v0.3-api` passed: about $0.35 and 7 minutes a case run, judge call
  not included.
- GitHub, Settings, Branches, `main`: require a pull request and "Require review from Code
  Owners".

## 1. Merge the 11 train/val drafts (owner)

The drafts are settled by tools (three judges and a tie-break); nobody reviews them.

```
for n in $(seq 7 17); do gh pr ready $n && gh pr merge $n --merge; done
git switch feat/v1-integration && git merge feat/golden-import
```

## 2. The reference check (2 classmates, about 20 minutes each)

**Done 2026-10-14:** from the 11 drafts as they are on the `review/case-*` branches (copied to
`.cache/reference_check/round1_drafts/`), seed 2, 20 + 10 items per classmate drawn exactly as on
2026-10-11, plus the RSB companion points the tie-break ruled on (re-ruled on 2026-10-14 with the
companion rule; `.cache/rsb_companions_v2/`). People judge one thing: did the AI read its source
faithfully. Drawn decisions resting on what that question cannot judge are taken out and listed
in the key (`.cache/reference_check/decision_basis.private.json`: a repeat of a kept item,
verified by code; not following from the articles; a kind the rules exclude). The reserve is
on the pages already, so it is one sitting: 30 and 29 points (46 answer-key points, 13 companion
points, scored apart); nobody is asked again. Each card marks what to judge: every board point on one
line naming the effect or risk, and the report points that mix in the law's duties on their claim
with those duties taken out (`.cache/reference_check/judge_lines.json`, written from the points' own
words); the rest of the point is grey context. People are scored against the AI's
answer to the same question on the same card (`rejudge`, done before any answer: the tie-break's
model with the page's rules, blind to the golden decision); the golden decision stays in the key
and on the review page. It differs from the golden decision on 21 of 59 items, mostly answer-key
points that add a detail the shown passage does not give. A pass therefore says people and the AI
read sources alike, not that the key is right: read the review page's "Where the answer key and
the page verdict differ" before publishing. Link to send to samuel and eetu:
`https://womm-api-production.up.railway.app/label/2026-10-14-75af237c`. Do not send the practice
links or the superseded `2026-10-11-86dae710`, `2026-10-13-db0f18f5`, `2026-10-14-db0f18f5`,
`2026-10-14-fca7564c`, `2026-10-14-3b23c3b9`, `2026-10-14-96254d61` (never sent). If a draft changes before publishing, draw a new check. How it was made:

```
uv run python scripts/reference_check.py sample .cache/reference_check/round1_v3_drafts/*.yaml \
    --annotator samuel --annotator eetu --n 20 --extra 10 --seed 2 --with-companions \
    --decision-basis .cache/reference_check/decision_basis.private.json \
    --judge-lines .cache/reference_check/judge_lines.json \
    --out .cache/reference_check/round1_v6/
uv run python scripts/reference_check.py extend --dir .cache/reference_check/round1_v6/
uv run python scripts/reference_check.py rejudge --dir .cache/reference_check/round1_v6/
uv run python scripts/reference_check.py upload --dir .cache/reference_check/round1_v6/ --url https://<app>
```

`upload` prints the link. `--annotator` is each
classmate's name (letters, digits, `-`, `_`); they sign in by typing it (case does not matter). Draw the reference-check sample before
publishing (step 4 deletes the drafts).

**Classmates:** open the link, sign in with their name, read the welcome card and the short
guide on the first item, answer every item, press "Finish & send". Answers are saved as they
go; they can stop and come back. Work alone, and don't discuss items until both have sent.
Progress is visible at `https://<app>/label/<sample>/admin` (asks for the admin token).

**No second round:** the reserve is already on the pages, so `score` decides on all items.

## 3. Read the review page (owner, about 5 minutes)

When the admin page shows both as sent:

```
uv run python scripts/reference_check.py fetch --dir .cache/reference_check/round1_v6/ --url https://<app>
uv run python scripts/reference_check.py score --dir .cache/reference_check/round1_v6/
```

`score` scores the two parts apart (the answer key, then the expert points) and writes
`review.html` and `review_companion.html` in its directory: the verdict in plain words with what to do
next, and every item where the people and the AI disagree, side by side with the AI's own
reason. Open it and mark who is right on each disagreement; the advice on the page updates.
This replaces reading eval transcripts by hand. The pages hold the AI's decisions: keep them
local, never send them. Record the reference check here:

| Date | Sample id | Error | 95% bound | Verdict |
|---|---|---|---|---|
| | | | | |

## 3b. RSB companions (after a ROBUST reference check)

The RSB opinions were fetched on 2026-10-12 (15 of 23 laws) and a companion draft of RSB
omissions was drafted for each of the 11 practice cases (31 points; 26 kept after the 2026-10-14 re-ruling with the companion rule, 18 before;
`docs/status/2026-10-12-rsb-companions.md`). They are drafted locally and not committed until
the reference check passes. Then, on the integration branch:

```
# merge the companion code into each review branch, then commit its companion
for b in $(git branch --list 'review/case-*' --format='%(refname:short)'); do
  git switch $b && git merge --no-edit feat/v1-integration && git switch -
done
# on each branch: copy the companion re-ruled on 2026-10-14 (kept in .cache/rsb_companions_v2/),
# check it, commit it (no new LLM call; same drafts, same result as the status report)
cp .cache/rsb_companions_v2/<case>.rsb.yaml evals/golden/drafts/
uv run python scripts/review_draft.py evals/golden/drafts/<case>.rsb.yaml --check
```

The disputed points were labelled in step 2; `score` there gave their verdict. The 17 points
all three judges accepted rest on step 3's measurement of that stratum; only if step 3 found an
error among the judges-accepted items, have them labelled too (`reference_check.py sample
evals/golden/drafts/case_*.yaml --annotator samuel --annotator eetu --companions --out
.cache/reference_check/companions/`, then upload). At most 10% wrong: the companions are
published with their cases. More:
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
The train eval is about 10 case runs, so about $4 to $5 at the pinned prices. The judge
calibrated here is `gpt-6.1-sol` at effort `medium`; changing either afterwards means a new
calibration sitting.

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
uv run python scripts/holdout_review.py --reference-check .cache/reference_check/round1_v6/ \
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

The val split has 3 cases, so 6 repetitions are 18 case runs: about $7 to $9 and, one case at a
time, about 2 hours.

```
uv run womm eval --system-version system_versions/v1.0-unscoped-api.yaml --split val --repetitions 6 --formal
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
