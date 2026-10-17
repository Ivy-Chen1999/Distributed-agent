---
title: "fix: Tighten the answer keys after the failed reference check (fidelity sweep, no repeat of the reference check)"
type: fix
status: active (Revision 2026-10-17 below; doc review of the revision pending)
date: 2026-10-16
origin: docs/status/2026-10-16-reference-check-result.html (reference check scored NOT ROBUST on 2026-10-16); owner decisions 2026-10-16
requirements: docs/brainstorms/2026-09-28-womm-phased-requirements.md R22, R23, R28, R30
amends: docs/plans/2026-10-04-001-feat-golden-case-expansion-plan.md (human check of train/val keys), docs/status/2026-10-07-v1-human-steps.md steps 2, 3, 3b, 4, 5, 6
---

# fix: Tighten the answer keys after the failed reference check

> **Revision 2026-10-17: what happened, and the pipeline fix** (doc review 2026-10-17 applied:
> coherence, feasibility). Where this block conflicts with a section below, this block wins.
> Names: the **window sweep** is U1-U3 below; the **full-text check** is the new check (F2).
>
> **What the window sweep got wrong.** It judged each point on the card's ~900-character window.
> Of the 55 points it flagged, the full-text reviewers kept 38 and proposed 17 edits; an earlier
> keyword-based review had been wrong on 5 of 8 proposed deletions. No window-only verdict may
> change a key. U2's automatic trim is withdrawn; its under-minimum rule (a case under 3 kept
> impacts is not published, `MIN_KEPT_IMPACTS`) stays and applies to every edit.
>
> **U0 and U3 as they ran.** The owner delegated the rulings (2026-10-16) to the assistant and
> Codex; no blind owner ruling and no owner keep sample exist. On 4 of the 24 errors, evidence
> outside the card window supports the key where the page AI's card verdict said `drop`. That shows
> a window check cannot settle the key; it does not show the page AI answered its card wrongly
> (boundary 1: people and the AI were judged on the same card). The remedy is the full-text check
> below, not a prompt fix. No second sitting.
>
> **Done (2026-10-16/17).** The disagreements were checked on the key side only; the annotators'
> answers and the NOT ROBUST result stay as recorded. 19 proposals (the reviewers' 17 from the 55
> flags, plus r56 and r59 from the reference check) went to Codex: 13 accepted, 4 rejected (the
> source supports them), 2 deferred. Written so far, 19 edits as `sweep` overlays keeping the
> original text: 12 trims, 1 restored "expected to" (r07, hedge), 4 rewrites bound to their
> sources (the 2 deferred RSB points and c10_m01, c10_o04 from Codex's follow-up), 2 drops; 15 in
> the train/val case drafts, 4 in the companions. Codex approved every edit (owner delegation).
> The 38 kept points had a screening, not a clause-by-clause review (open). Wording everywhere:
> "checked by two people on a sample, which failed; corrected after their feedback by an AI
> full-text review; not re-checked by people". This replaces decision 3's sentence below.
>
> **The pipeline fix.** Traces: R22 (keys say only what the IA or the proposal's text supports;
> an inference only where the item rules allow it), R23 (holdout sealed), R28 (promotion on a valid
> holdout key), R30 (what the demo may claim).
>
> - **F1 Sources per fixture.** `ia_full.txt` (the IA with its annex volumes), `rsb_opinion.txt`
>   (falling back to `rsb.txt`, the fallback marked in every result) and the proposal's articles
>   (`data/fixtures/<f>/proposal.json`). Each source's sha256 is recorded with every result.
> - **F2 The full-text check** (new role `key_check` in `evals/drafting.yaml`, claude-sonnet-5,
>   not the drafter's model; prompt `prompts/tools/key_check.md`). The roles have no tools, so
>   retrieval is in Python: (1) a call splits the point into claims (group, effect, direction,
>   figure, time, policy option, cause, modality) and writes 3-5 search phrasings per claim;
>   (2) passage retrieval over F1's sources (paragraph-level, case- and inflection-normalised,
>   "Commission" never matching "commission") returns the top passages per claim; (3) a call
>   judges each claim on those passages: `supported` (quote and file:line, typed `ia`, `law` or
>   `inference`), `not_found`, or `cant_tell`. A quote supports a claim only if it matches its
>   group, option, time, direction, amount, cause and modality. `not_found` never edits anything
>   by itself: every proposed edit goes to Codex. A board point whose opinion text is missing
>   gets `cant_tell`.
> - **F3 Modality is part of the claim.** A prediction stated as fact fails (case m01).
> - **F4 Acceptance.** (a) Data, in the unit tests: each of the 27 cases in
>   `evals/regression/key_fidelity_2026-10-17.yaml` gets a frozen input (the full point text
>   before any fix, the fixture, each source's sha256) in the same file, and `claim` names the
>   clause the case judges: one point can hold a supported and an unsupported clause (w12, o01),
>   so a verdict is compared clause by clause, never for the point as a whole. (b) Behaviour, offline on
>   the subscription: F2 on those frozen inputs gives every `expected` verdict in each of 2 runs;
>   any miss is fixed and the 2 runs repeated. The two runs are independent: the second reads
>   nothing the first produced.
>   **Changed 2026-10-17 (owner decision, after four versions failed "every case right"):** each
>   point's claims are judged 3 times independently on the same evidence; a verdict stands only
>   when all 3 agree, otherwise `cant_tell`. Acceptance: in each of 2 independent runs, no
>   dangerous miss (`supported` for an unsupported claim, `not_supported` for a supported one, or
>   a split element never checked); `cant_tell` is allowed, reported as a rate, and every
>   `cant_tell` goes to Codex. Agreement shows a verdict is stable, not that it is right (Codex):
>   stable errors are fixed first, an agreed verdict still has its evidence reviewed before any
>   edit, and nothing edits a key automatically. The cases (now 31, plus 5 full-point split cases)
>   were used to debug the check: they are a regression set, and the check is tested for real on
>   the 38 open points. Two of the 27 are legal background that must be
>   kept (CRA class I/II scope, c04_e03; PLD trade secrets, c11_o01): a check that drops what the
>   law states fails. The result is recorded next to the cases before F2
>   is used on any draft.
> - **F5 Re-check and record.** F2 runs on the 38 kept points, the 19 already-edited points and a
>   seeded 10% of the never-flagged keeps per draft; Codex reviews every proposed edit and every
>   `cant_tell`. A second edit of an overlaid item keeps the original `before` and replaces the
>   text. The record (`key_checked.json`; train/val in the reference-check dir, holdout in
>   `.cache/holdout_sweep/`) holds: sample, basis `corrected after feedback, not re-checked`, check
>   model and prompt hash, source sha256s, the F4b result, counts, the reviewer, and every checked
>   draft's digest.
> - **F8 The gate.** A new record type, not a change to the old counts: the window sweep's
>   `key_tightened.json` and its U0/U3 rules stay as written and are not used for this sample.
>   `robust_reference_check` passes the pinned sample only with a valid `key_checked.json`:
>   the **check version** (model, prompt hash, and the hash of the retrieval code and its
>   configuration: a change to any of them needs a new F4b run) matches the version that passed
>   F4b, F4b passed, every source
>   sha256 and every checked draft's digest are current, every proposed edit and every
>   `cant_tell` has a recorded ruling (train/val: Codex; holdout: the owner only, boundary 3), and the record names the failed reference check it
>   follows. The reference check's own result (NOT ROBUST) is never rewritten. Tests: each
>   condition refused alone; a valid record passes with basis `corrected after feedback, not
>   re-checked`.
> - **F6 Drafting, for new drafts only.** `draft_golden_case.md` and the tie-break emit claims with
>   evidence per claim, typed as in F2, and keep modality. The new field is optional and left out
>   of the item digest when unset, so existing drafts and digests hold. Existing keys are covered
>   only by F5.
> - **F7 The labelling page.** "Can't tell" gets an optional reason (missing material, unclear
>   scope, unknown term); "the source does not support it" stays **No**; the three answers do not
>   change. Built before the calibration sitting.
>
> **Boundaries.** (1) Full-text evidence is for reviewing the key; when people and an AI are
> compared on cards, both see the same material, and no full-text finding marks the people wrong.
> (2) A full-text review never says the card showed enough. (3) Holdout: F2 runs on the owner's
> machine with tracing off, outputs only under `.cache/holdout_sweep/`; only the owner reviews its
> findings, and nothing from them feeds development, prompts or the acceptance cases. (4) The gate
> stays pinned to sample `2026-10-14-75af237c`.
>
> **Human steps to change (U7):** step 3b (companions wait for the F5 record, not a ROBUST check);
> step 4 (publishing does not wait for F5: the corrected keys are published now and F5's later
> edits are re-published as a new version, so calibration keeps its 2026-11-06 cut-off); step 5
> (the tightened basis replaces ROBUST; the holdout F2 check replaces the tie-break re-run; the spot
> check asks 5 items per draft, not 3).
>
> **Order.** F4a and F1 first, then F2 and F4b (about two days on the subscription, retrieval
> included), then F5 (train/val, then the owner's holdout run), F7 before the calibration sitting,
> F6 before the next drafting round.


## What happened

The reference check (sample `2026-10-14-75af237c`, 59 items, two classmates) scored NOT ROBUST:
52.2% error on the answer-key points (24 of 46, 95% bound 63.8%) and 23.1% on the RSB companion
points (3 of 13). The error counts the 12 answered disagreements and the 12 "can't tell" answers.
The assistant's first-pass reading of every error and every key-vs-page difference
(`docs/status/2026-10-16-reference-check-result.html`, not the owner's ruling) found:

- **The key over-reaches.** About 11 of 46 kept answer-key points (24%) and 1 of 13 companions
  carry one clause their source does not state: an added effect, cause, actor or figure ("offer
  lower prices", "because the control criteria give legal certainty"). The tie-break accepted
  them because the rest of the point matches its anchor. The page's AI answer (the **fidelity
  check**, `prompts/tools/fidelity_check.md`, run on the tie-break role's model) caught them;
  most people kept them.
- **"Can't tell" is mostly a misread task.** In 8 of 12 the figure is in the highlighted sentence;
  the note asks where the report got the number, which the card does not ask.
- **The fidelity check's reading holds** on about 20 of the 24 errors; about 4 are unclear and
  none is plainly wrong in the first pass.

## Owner decisions (2026-10-16)

1. **No repeat of the reference check.** The classmates do not label a new reference-check
   sample. (The calibration sitting in step 4 is a different task and still happens.) This
   replaces the human-steps rules "if a draft changes before publishing, draw a new check" (step
   2) and "NOT ROBUST: settle the drafts again and run a new check" (steps 3 and 3b) for this
   sample only.
2. **Sweep every point, not only the sampled ones.** The over-reach is a drafting pattern, so the
   unsampled points carry it too. Every kept point of the 11 train/val drafts, their companions
   and the 8 holdout drafts is checked with the page's own question, and an over-reaching point
   is trimmed to what its source states, or dropped. For the holdout this sweep stands in for
   re-running `tiebreak_golden_case.py` (step 5): the error the check found is over-reach in kept
   points, which the sweep targets; a re-run of the same tie-break prompt would repeat it.
3. **Change the rule, weaken the claim.** Steps 3b, 4 and 5 no longer need a ROBUST reference
   check for this sample. They need a **tightened key** (U4). The demo says: "Two people checked
   a sample of the answer key; it failed at 52%. A model check then flagged points that add to
   their source, and those were trimmed or dropped. The owner reviewed every edit and a sample of
   the unflagged points. The key was not re-checked by people, and missing points were not
   checked." Never "read-checked by two people" and never "robust".

## Requirements trace

- **R22** (15-30 golden cases from COM proposal + IA pairs): the keys must say only what the IA
  says, or coverage rewards and punishes things the source does not contain.
- **R23** (holdout keys sealed, seen only by the promotion process): the holdout drafts went
  through the same drafting and tie-break, so they get the same sweep, run by the owner. As in
  drafting, holdout cards go to Anthropic through the Claude CLI on the subscription; nothing is
  traced, nothing is written outside `.cache/holdout_sweep/`, and the assistant never reads it.
- **R28** (promotion on the holdout): a key with over-reaching points makes holdout coverage
  partly unreachable for any version, which shrinks real deltas toward zero.
- **R30** (the demo cycle): what the demo may say about the key it promotes on is decision 3.

## Implementation units

### U0. Owner's blind ruling on the 24 errors (step 3, before any sweep)

- The review page shows each of the 24 errors with the AI's reason hidden until the owner has
  marked "people right", "AI right" or "unclear"; the first mark is locked once the reason shows
  (`review_page.html` today shows reasons at once and lets marks toggle, :173-183). The 21
  key-vs-page cards get their own buttons ("key right", "key wrong", "unclear"; today they have
  none, :234). A copy button exports the counts as JSON, which `--apply` reads into the record.
  This is the only independent evidence on the fidelity check.
- **Stop rule:** if the owner marks the AI wrong on more than 3 of the 24, fix the fidelity
  prompt and re-run it on these 24 cards first (one retry), before U1. The first pass expects 0
  wrong and about 4 unclear.
- The owner also rules the 21 key-vs-page differences, as step 3 already asks; the count of
  "key wrong" is recorded and is expected over 10% (about 12 of 59): that is why U1-U3 run.
- About 45 minutes, as in step 3.

### U1. Fidelity sweep (`scripts/key_fidelity_sweep.py`, `womm.eval.fidelity`)

- **Swept set:** for a case draft, `golden_review.kept_impacts(draft)` plus omissions with a kept
  decision; for a companion, its kept omissions. Items are built as `reference_check.Item`
  directly (not through `population()`, which skips human-decided and holdout items).
- **Cards:** built with `reference_check.card`, the labelling page's passage window. Inputs
  `--judge-lines <json>` for the points that already have one. A card is judged on the fields the
  page marks as the point (`fidelity.card_input`); `mechanism` stays context only, so over-reach
  in `mechanism` is out of scope and the demo wording says only "points that add to their source".
- **Verdicts:** `fidelity.FidelityVerdict` from the tie-break role (claude-sonnet-5, a different
  model from the drafter), 15 cards per call, the page's prompt unchanged.
- **Output:** `sweep.private.json` (per item: verdict, reason) and a count line per draft.
- **Holdout:** `--holdout` runs inside `tracing_context(enabled=False)`, as the other holdout
  scripts do, writes only under `.cache/holdout_sweep/`, and prints counts only. A holdout draft
  without `--holdout` is refused.
- **Tests:** a fake backend; every swept item gets one card and nothing else does; a missing
  verdict fails the run with nothing written; the holdout refusal; holdout output holds no item
  text on stdout.

### U2. Trim (`--trim`, `prompts/tools/trim_point.md`)

- For each `drop`, the drafter role (claude-opus-5-5) gets the card and the reason (role name
  `fidelity_trim`, output `TrimOutput{item_id, action: trim|drop, fields}`). A trim may only
  delete words from the fields judged as the point (`affected_actor`, `impact`, `description`);
  it adds and rewords nothing. A judge-line card (board points, report points mixing in the
  law) is never trimmed: a `drop` there is proposed as a drop of the whole point.
- The trimmed card (`card()` on a `model_copy` of the item) is checked again with the fidelity
  check. Still `drop`: proposed for dropping.
- **Output:** `edits.private.json` (before, after, both reasons) and a local before/after page
  (`edits.html`). No draft is written. Before any write, U2 reports every case whose kept
  impacts would fall under `MIN_KEPT_IMPACTS` (3); the owner chooses keep-untrimmed or retire.
- **Tests:** a trim is a deletion only (each after-field is a word subsequence of its before);
  a second failing check becomes a drop proposal; a judge-line card is never trimmed; the
  under-minimum report.

### U3. Owner review and apply (`--apply`)

- The page lists every proposed trim and drop, plus a seeded sample of the points the sweep kept
  (10% per draft, at least 2). The owner accepts or rejects each edit and marks any sampled keep
  that is wrong. **Rule:** more than 1 wrong keep per 15 sampled stops the apply: the sweep's
  keeps are not trusted; fix the prompt and re-sweep.
- `--apply` records each accepted edit as a **sweep overlay** on the item, a new optional field
  `sweep: SweepBlock{action: trim|drop, before: {field: text}, reason, model, date}` on the
  draft item models. The item's `review` (decision, reviewer, audit), its `tiebreak` block and the
  draft's audit samples stay untouched, so the drafter rule (the drafts' `drafted_by` is the
  owner), the audit tally, the tie-break audit recomputation and the "settled by the tool" rule
  are unaffected (round-2 review: re-using `TiebreakBlock` fails `tiebreak_problems`,
  golden_review.py:508-529).
- Code changes that read the overlay: `integrity_problems` checks the item digest against
  `sweep.before` and checks each after-field is a word subsequence of its before-field (a
  deterministic deletion-only check); `kept_impacts` and the omission publishing leave out
  `action: drop`; the published case carries the trimmed text and a `tightened` note;
  `spot_sample` draws only items without an overlay.
- Human-decided items (`verified`, `edited`, `human_added`) are swept and edited the same way:
  the overlay sits beside the person's decision, which stays on record.
- `key_tightened.json` (in the reference-check dir for train/val; in `.cache/holdout_sweep/` for
  the holdout) records: the sample id, each swept draft's digest after apply, counts (swept,
  kept, trimmed, dropped, owner rejected, sampled keeps, wrong keeps), the owner's U0 counts
  (AI wrong, unclear) and the owner's name and date. No item text.
- **Tests:** apply round trip on a fixture draft; a rejected edit leaves the item unchanged; the
  wrong-keep stop; `review_draft.py --check` passes on the applied fixture; no item text in the
  record.

### U4. The gate (`scripts/verify_golden_case.py`, `scripts/holdout_review.py`)

- `robust_reference_check(dir)` returns `(sample_id, basis)` with basis `robust` or
  `tightened, not re-checked`. NOT ROBUST passes only when all hold: the sample is
  `2026-10-14-75af237c` (pinned in code; any later failed check needs a new owner decision and
  plan), `key_tightened.json` is present with U0 within the stop rule and U3 within the wrong-keep
  rule, and every draft being checked has the digest the record names (a draft changed after
  the sweep is refused). The digest check runs in `spot_verify` and `holdout_review.py`, where
  the draft paths are known. Train/val publishing (`publish_golden_cases.py`) has no
  reference-check gate in code today; it gains the same record check.
- For the holdout, `holdout_review.py` also requires `.cache/holdout_sweep/key_tightened.json`
  covering all 8 drafts by digest.
- `spot_verify` writes the basis into the case notes and the handoff record (today it writes
  "was ROBUST"). Every caller prints the basis.
- **Holdout spot check size:** with the basis `tightened, not re-checked`, the spot check asks 5
  items per draft (was 3; `SPOT_SAMPLE` becomes a parameter chosen by the basis), all from items the sweep left unchanged; the swept edits were already
  reviewed one by one in U3.
- **Tests:** ROBUST passes; another sample's NOT ROBUST is refused; the pinned sample without a
  record, with a record over either rule, or with a changed draft digest is refused; with a valid
  record it passes and the note says "tightened, not re-checked"; the holdout record is required.

### U5. Holdout

- The owner runs U1-U3 on the 8 holdout drafts with `--holdout` after the train/val apply and
  before step 5, on the owner's machine. Then step 5 as amended in U4.

### U6. The calibration sitting

- The calibration page (step 4, the same two people) gets one line on its welcome card: "Judge
  only what the card asks. A figure quoted from the report needs no further source." The
  calibration question differs (is an expected impact covered by a dossier), and its own review
  page lets the owner rule each disagreement, as it does now. The reference-check labels are not
  used to judge these two people; the reference check's failure is mostly over-reach in the key,
  which the sweep removes.

### U7. Docs

- Human steps: steps 2, 3, 3b, 4 and 5 as amended above; step 6's demo sentence ("AI-drafted,
  read-checked by two people") becomes the decision-3 wording; the reference-check table keeps the
  NOT ROBUST row and gains the U0 and U3 counts.
- Talk tracks (`docs/demo/outcome-scripts.md`), hard questions (`docs/demo/hard-questions.html`)
  and the runbook: every "read-checked" becomes the decision-3 wording.
- The sweep's per-case before/after point counts go on the result page.

## Order and cost

1. U0 (owner, about 45 minutes).
2. Step 1: merge the 11 review branches (their drafts are byte-identical to the copies the check
   used, checked 2026-10-16), then U1-U3 on `evals/golden/drafts/` of `feat/golden-import`, one
   commit.
3. U4, U6 and U7 code and docs on `feat/v1-integration`, before step 4.
4. U5 by the owner before step 5.

The model calls run on the subscription (no API cost): about 10-15 sweep calls for train/val,
about 5 for the holdout, one trim call per flagged point. The owner's time: U0 about 45 minutes,
U3 about 30 minutes for train/val and 20 for the holdout. Calibration keeps the 2026-11-06
cut-off; if U0's stop rule fires, its one retry fits before 2026-10-23.

## Risks

- **Circularity.** The key is tightened by a Claude model check whose reading the people
  disagreed with. The independent evidence is the owner's blind U0 ruling and the U3 keep sample,
  both recorded; the demo wording names a model check. The cross-family rescore (plan
  2026-10-15-001) scores against the tightened key; its "disagreement is the signal" reading is
  noted as weaker for that reason.
- **One-way sweep.** Only kept points are re-examined and edits only shrink the key: rejected
  points and missing points are not revisited, and coverage gets easier for every version. The
  result page shows per-case counts before and after; a case under 3 kept impacts is not
  published (U2).
- **A deletion can still change meaning** (a removed qualifier). The owner reviews every edit.
- **The published cases differ from what the classmates saw.** The check is on record as failed
  on the untrimmed key; the demo wording says so.
