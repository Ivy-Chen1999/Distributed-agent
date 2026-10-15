# WOMM v1: the steps that need a person — 2026-10-07 (revised 2026-10-14)

Everything else in v1 is automated. People are needed where an LLM is being checked and another
LLM cannot be the reference: did the AI read its sources faithfully (the reference check; it does
not show that the answer key is right, see step 3), does the AI judge score like people do (the
calibration), and is the holdout key sound (the owner's spot check, step 5). The first two are a
sitting by the two classmates on the deployed app; the third stays with the owner. The rest of
this page is the owner's: permissions, one signature, and commands that run unattended. Commands
refer to `feat/v1-integration`, except step 8 (`feat/eu-cost`).

Data boundary: the holdout store and the step 5 page stay on the owner's machine. The api versions
run on OpenAI, so a formal holdout comparison sends holdout proposal text and answer-key items to
the OpenAI API (system roles and judge), and the cross-judge re-score after it sends the stored
holdout dossiers and key items to Anthropic through the isolated Claude CLI. Committed files (`evals/promotion_records.yaml`, this page)
carry aggregate holdout numbers and counts only, never holdout case ids, proposal names or key text.

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
| 3 | Read the reference-check review page, mark who is right, rule on the 21 key differences | owner | about 45 min |
| 3b | RSB companions: commit them on `feat/v1-integration` before step 4 (labelled in step 2) | owner | 10 min |
| 4 | Publish the golden cases; then the calibration sitting | owner, then 2 classmates | 5 min, then about 20 min each |
| 5 | Holdout: spot-check 3 items a draft on a local page (tie-break **done 2026-10-12**), import | owner | about 20 min |
| 6 | Sign the promotion policy (**done 2026-10-12**) | owner | 5 min |
| 7 | R34 noise run and MDD report (after the calibration passes and step 5) | owner starts it | 10 min plus about 2 h |
| 8 | Cost reference: run the check, record the FTE basis (**done 2026-10-12**) | owner | 5 min |

Order: 0, 1, 2, 3, 3b, 4, 5, 7; then the first holdout comparison. The calibration in 4 needs the
published cases and the API key (the gate calibrates the API judge, and the published cases give
it 13 cases instead of 2). 5 needs a passed reference check. 7 needs the published val cases (step
4), a recorded calibration (a judge-prompt fix after a failed calibration changes the judge id,
and R34 records are looked up by judge id) and step 5's holdout count. 6 and 7 come before any
holdout comparison; 8 any time after 0.

Cut-off: the reference check and the calibration each get at most one retry, and both pass by
2026-11-06. If either still fails then, the demo cycle runs with the existing key and judge, and
the gate decision is labelled with the failed check by name.

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
- API key, for step 4 and formal gates: set a monthly spend limit in the provider console first,
  then put the key in the local `.env` only. **Done 2026-10-14:** the OpenAI key is in the local
  `.env`. The api versions are pinned (on `feat/v1-integration`, `system_versions/PROMOTIONS.md`):
  every role on `gpt-6.1-sol`, reasoning effort `medium`. A live run of `case_02` on `v0.3-api`
  passed: about $0.35 and 7 minutes a case run, judge call not included (measured with the
  Planner still on `gpt-6-luna`; about a cent more on sol).
- Still to do for the key: a monthly limit of at least $60 (R34 plus one holdout comparison with
  headroom; a limit hit mid-run returns 429 and aborts the run after its spend), checked before
  step 7 and each comparison; the account's API data settings checked and noted here (no training
  on API data; zero retention if offered). The key is never set on Railway: the deployed
  `womm-api` makes no model calls. Rotate it if `.env` or a run report is ever shared.
- Tier: every formal run (step 4 calibration eval, R34, holdout comparisons) uses the standard
  tier. Flex is not wired in: the code has no tier setting, and a 429 aborts the whole eval.
- GitHub, Settings, Branches, `main`: require a pull request and "Require review from Code
  Owners".

## 1. Merge the 11 train/val drafts (owner)

The drafts are settled by tools (three judges and a tie-break); nobody reviews them.

```
for n in $(seq 7 17); do gh pr ready $n && gh pr merge $n --merge; done
git switch feat/v1-integration && git merge feat/golden-import
```

## 2. The reference check (2 classmates, about 20 minutes each)

**Done 2026-10-14:** from the 11 drafts as they are on the `review/case-*` branches (copied with
their companions to `.cache/reference_check/round1_v3_drafts/`), seed 2, 20 + 10 items per classmate drawn exactly as on
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

## 3. Read the review page (owner, about 45 minutes)

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

| Date | Sample id | Error | 95% bound | Verdict | Key errors in the 21 differences |
|---|---|---|---|---|---|
| | | | | | |

Then read "Where the answer key and the page verdict differ" (21 of 59 items) and rule on each
against the source: key wrong, or AI answer wrong. Count the items ruled key wrong. Up to 10% of
the answer-key points (the companions' bound): fix those items, then publish. More: the key is
not ready; fix the drafting input and settle the drafts again before step 4. Record the count in
the table; the demo then says the key was "read-checked by two people, owner-ruled on the
differences", nothing stronger.

## 3b. RSB companions (after a ROBUST reference check)

The RSB opinions were fetched on 2026-10-12 (15 of 23 laws) and a companion draft of RSB
omissions was drafted for each of the 11 train/val cases (31 points; 26 kept after the 2026-10-14 re-ruling with the companion rule, 18 before;
`docs/status/2026-10-12-rsb-companions.md`). They are drafted locally and not committed until
the reference check passes. Then, on `feat/v1-integration` after step 1's merge and before step
4 (the review branches are already merged in step 1, so commits there never reach the published
cases; `publish_golden_cases.py` publishes a case without its companion and refuses a companion
that arrives later):

```
# copy each companion re-ruled on 2026-10-14 (kept in .cache/rsb_companions_v2/), check it,
# commit it (no new LLM call; same drafts, same result as the status report)
git switch feat/v1-integration
cp .cache/rsb_companions_v2/<case>.rsb.yaml evals/golden/drafts/
uv run python scripts/review_draft.py evals/golden/drafts/<case>.rsb.yaml --check
git add evals/golden/drafts/*.rsb.yaml && git commit -m "feat: RSB companions for the 11 train/val cases"
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
# case drafts only (a companion holds omissions only and fails as a case); the api judge
uv run python scripts/oracle_check.py --system-version system_versions/v1.0-unscoped-api.yaml \
    $(ls evals/golden/drafts/case_*.yaml | grep -v '\.rsb\.yaml$')
uv run python scripts/publish_golden_cases.py
uv run python scripts/oracle_check.py --system-version system_versions/v1.0-unscoped-api.yaml   # published cases, companions included
```

Commit the published cases. Then the calibration (needs the OpenAI key in `.env`): one train
eval run with the api incumbent of the gate (`v1.0-unscoped-api`, the outputs the judge will
grade), 30 pairs that both classmates label, about 20 minutes each. The train eval is about 10
case runs, so about $4 to $5 at the pinned prices, judge calls included. The judge calibrated
here is `gpt-6.1-sol` at effort `medium` (decided 2026-10-14, kept although it is the same model
as the system under test; see step 6 for the cross-family check); changing either afterwards
means a new calibration sitting.

```
uv run womm eval --system-version system_versions/v1.0-unscoped-api.yaml --split train --local
uv run womm calibrate sample --sv system_versions/v1.0-unscoped-api.yaml --report runs/<that report> \
    --n 30 --seed 1 --annotator samuel --annotator eetu --out .cache/calibration/round1/
uv run womm calibrate upload --dir .cache/calibration/round1/ --url https://<app>
# when both have sent:
uv run womm calibrate fetch --dir .cache/calibration/round1/ --url https://<app>
uv run womm calibrate score --dir .cache/calibration/round1/ --record
git commit evals/promotion_records.yaml
```

- Calibration counts only when the two classmates agree with each other on at least 80% of the
  pairs and at least 24 of the 30 have a clear answer; then the judge must agree with them on
  at least 85%. Passed: recorded, and the statistical and weak gates can run once step 7 is
  recorded. People below 80%: the rule is unclear; sharpen its wording from the split pairs on
  the review page, then a new seed. Judge below 85%: fix the coverage judge's prompt, then a new
  seed. One retry only (cut-off above). With 30 pairs, a judge whose true agreement is 85% passes
  about half the time (90%: about 4 in 5); a failed sitting is noted in this page with its score.

## 5. Holdout (owner only, about 20 minutes)

The holdout store and this page stay on the owner's machine, so this cannot go to classmates
(formal comparisons later send holdout text to OpenAI; see the data boundary at the top). It
needs a ROBUST reference check (step 3): the same tool chain then settles the holdout drafts, and
the owner only checks 3 items a draft (one per kind of decision), about 24 in all. The spot check
stays human: no model replaces it, because the system under test and the judge run on the same
model. The 2026-10-12 tie-break rulings count only if step 3 is ROBUST; after a NOT ROBUST
result, settle the drafts again and re-run `tiebreak_golden_case.py` before this step.

Run in the integration worktree (the 8 drafts are in its `.cache/drafts/`; the reference check
is in the main checkout's `.cache/`). Not imported yet (checked 2026-10-14: 8 drafts, empty
`.cache/holdout_import/`).

```
uv run python scripts/holdout_review.py \
    --reference-check /Users/yuxin.chen/Distributed-agent/.cache/reference_check/round1_v6/ \
    --reviewer "Chen Yuxin"     # opens a local page with every draft's 3 items, blind
HOLDOUT_DATABASE_URL=... uv run python scripts/import_holdout_case.py .cache/holdout_import/<case>.yaml
```

The page runs on this machine only (127.0.0.1), never on Railway. The browser's translate button
works on it; it sends the page text to the translation service, so use it only if that is
acceptable for the holdout.

Any disagreement with the tool on a draft refuses the spot check (the same rule as the
reference check's early stop; the holdout is newer material than train/val); then run
`holdout_review.py ... --full <case_id>` for that draft (every item, same page). The step is done
when `.cache/drafts/` is empty and the holdout store reports 8 cases from 8 proposals.

## 6. Sign the promotion policy (owner)

`evals/promotion_policy.yaml` carries the plan's defaults (coverage primary; grounding may drop
at most 0.02; omissions at most one noise SD; `expected_gain` 0.08; dev-mode comparisons count
against the budget of 6). Change a value first if you disagree, then:

```
uv run womm policy sign --by "<your name>"
git commit evals/promotion_policy.yaml
```

Never change the file after a comparison; every decision records its sha256.

**Decided 2026-10-14, before any comparison** (plan
`docs/plans/2026-10-14-001-feat-precision-guard-and-adaptive-repetitions-plan.md` on
`feat/v1-integration`; the policy gains the fields below, its signature is cleared, and the owner
signs again (`womm policy sign`)):

- Repetitions adapt to the data: each comparison runs 3 per case and arm, and extends both arms to
  6 only when the pooled noise (never the difference between the arms) says 6 can resolve
  `expected_gain` and 3 cannot. The decision records the repetitions used.
- Weak mode promotes only when the coverage gain exceeds one standard error of the comparison's
  own noise (`weak_min_delta_se: 1.0`), not merely 0. The R34 report states how often a version
  compared against itself would clear that bar (the null pass rate); the demo quotes it.
- Precision guard (binding): the share of dossier impacts the judge matched to an expected impact
  may drop by at most one noise SD (`precision_regression`). Impact count and total impact text
  are reported for both arms, with a note above +25%; they reject nothing.
- At most 2 formal holdout comparisons in the demo; the second only for the prompt-only fallback
  candidate. A rejection by the gate is shown as a valid end of the cycle.
- Before spending a comparison, the chosen candidate's api twin runs once on val (about $3), so a
  gain found on the Claude subscription is confirmed under the GPT model and judge.
- After each comparison, a report-only check: the coverage delta re-scored by a Claude judge
  (`claude_code`, subscription, about 1-2 hours, no API spend) on the same stored runs:
  `uv run womm evolve rescore <gate_id>` (resumes after a usage limit). Opposite signs, or a
  promotion the Claude judge does not see as a gain, label the decision `cross-judge-sensitive`;
  the decision itself is unchanged. Agreement is weak evidence (the dev cycle tuned the candidate
  against a Claude judge); disagreement is the signal. Plan:
  `docs/plans/2026-10-15-001-feat-cross-family-judge-rescore-plan.md` on `feat/v1-integration`.
- Not adopted: full human verification of every holdout item, and extra calibration pairs from the
  candidate's output. The demo says the key is "AI-drafted, read-checked by two people,
  owner-ruled on the differences, holdout spot-checked" and the judge "calibrated on the
  incumbent's output". Every reason a decision can carry, and what it does not mean, is in the
  runbook's reject-reason table.

## 7. R34 noise run and MDD (owner starts it)

After the calibration is recorded and after step 5 (the val cases exist only once step 4 has
published them). It starts at 3 repetitions (9 case runs, about $4, about 1 hour) and the report
says `stop` or `extend`; on `extend`, 3 more (about $4 more), pooled with the first. Every formal
run is logged in `evals/promotion_records.yaml` when it finishes; commit that file after each run
(the next formal run refuses a dirty tree). Only the first chain for a judge counts, and the
report shows the stop rule's numbers only when it records. Standard tier.

```
uv run womm eval --system-version system_versions/v1.0-unscoped-api.yaml --split val --repetitions 3 --formal
git commit evals/promotion_records.yaml -m "chore: formal R34 run logged"
uv run womm noise report --report <first report> --holdout-cases 8 --holdout-proposals 8 --record
git commit evals/promotion_records.yaml -m "chore: R34 first attempt"
# only if it says extend (same code; commits that touch only the records file are allowed)
uv run womm eval --system-version system_versions/v1.0-unscoped-api.yaml --split val --repetitions 3 --formal
git commit evals/promotion_records.yaml -m "chore: formal R34 run logged"
uv run womm noise report --report <first report> --report <second report> \
    --holdout-cases 8 --holdout-proposals 8 --record
git commit evals/promotion_records.yaml -m "chore: R34 extension"
```

If the code changed between the two runs, run one fresh 6-repetition run instead and record it on
its own (it is logged as such). Sign the policy before the first R34 run: the stop rule records
the policy's hash, and a later policy change blocks the extension.

If the MDD is above `expected_gain`, the gate uses the weak threshold, and the demo says so. An
MDD at or below `expected_gain` is necessary, not sufficient: it covers run-to-run noise only, not
how a gain varies between cases, so with an MDD near 0.08 statistical mode is still likely to
reject a real 0.08 gain.

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
