# Golden-case review guide

First review? Read the one-page [reviewer quickstart](reviewer-quickstart.md) first; this guide
holds the full rules.

> **Since 2026-10-08 a person reviews nothing by default.** A tie-break judge
> (`scripts/tiebreak_golden_case.py`, `womm.eval.tiebreak`) settles every item the three judges
> left open and every `possibly_missing` candidate: `llm_kept` or `llm_dropped` (unsure items
> are dropped). The audit samples (`audit: true`: 20% of the judges' auto-accepted items and 20%
> of the tie-break-kept items) are optional spot checks (`AUDIT_BLOCKS` off). The rules below
> apply to the items a person does decide: spot checks, `human_added` items, any item of an
> escalated proposal, and drafts made before the tie-break existed.

You are checking reference answers that an LLM drafted from an official impact assessment (IA).
WOMM is scored against these answers, so a wrong item teaches the wrong lesson. Plan on about
5–8 minutes per case. Post your minutes as a PR comment; the case owner logs them in
`evals/annotation-assignments.md`, so the review PR keeps only the draft.

## What you review

A draft lives in `evals/golden/drafts/<case_id>.yaml` on its own `review/<case>` branch, opened
as a draft pull request. That PR contains only the draft. Code changes go in separate PRs, which
must never contain an unreviewed draft. Each item has:

- the claim: `affected_actor`, `mechanism`, `impact` (or `description` for omissions);
- the evidence: `ia_section` and `ia_anchor`, a verbatim quote from the IA;
- `provision_keys`: the articles of the proposal the impact follows from;
- `category` and a `derivability` pre-check;
- the verdicts of three separate LLM judges (anchor, derivability, category);
- a `review` block.

**Only review items that need a human:**

- items whose `review.decision` is `pending`. These are judge disagreements or uncertain items,
  flagged items, and the random 20% audit sample (`audit: true`). The sample is drawn only from
  auto-accepted impacts and omissions; candidates always need you anyway;
- every `possibly_missing` candidate, even when it shows `auto_accepted`.

The draft's `stats.human_decisions_needed` counts exactly these items (pending items plus every
candidate), so it tells you the size of the review before you start.

Leave the other `auto_accepted` items alone. There is one exception: if the CI check says the
proposal is **escalated**, every item needs a decision. A proposal is escalated when more than
10% of its audited items were edited, rejected or marked unclear. The count spans PRs: when a
draft is published, its audit counts go into `evals/golden/audit_tally.yaml` (counts per
proposal only), and later drafts of the same proposal are judged on the running total.

To check an item, open the IA and search for the anchor quote. Read the paragraph around it.
The PR description and the draft's `review_links` block link the IA and the proposal on EUR-Lex
(train/val cases only; holdout identifiers never appear in a tracked file). For an IA in several
parts, `review_links.anchor_parts` names the part that holds each item's anchor.
`uv run python scripts/review_draft.py` lists every item you must decide with its IA section, the
words to search for, and the judges' reasons.

## Decisions

Set `review.decision` to one of the values below, and set `review.reviewer` to your GitHub
username.

| Decision | When | Note |
|---|---|---|
| `verified` | The item matches the IA (see below) | optional |
| `edited` | The item is right in substance, but a field needs fixing. Fix the field in place | say what you changed |
| `rejected` | The item is wrong, or fails one of the reject rules below | **required** |
| `unclear` | You cannot tell, or two careful readers could reasonably disagree | **required** |

Rejected and unclear items are dropped when the case is published. An item that a human finds
unclear is not a fair test, so it is not kept as a coin flip.

## What counts as matching the IA

All four must hold:

1. **Actor.** The affected actor is the one the IA names, at the same level of detail. For
   example, "providers of high-risk AI systems" is not the same actor as "all AI providers".
2. **Mechanism.** The provision–behaviour link is the one the IA describes, not a plausible
   alternative.
3. **Impact.** The direction and the kind of effect match the IA (cost or benefit, one-off or
   recurring, and who bears it).
4. **No changed numbers.** Every figure, range and unit is exactly the IA's. Rounding "EUR 6 000–7 000"
   to "about EUR 7 000" is an edit. A number the IA does not give is a reject, unless you
   remove it with an edit.

## Reject when

- The anchor quote does not support the claim, or the claim adds something the anchor does not
  say.
- The impact belongs to a different policy option than the one the proposal implements.
- The impact rests only on **annex content of the proposal**. Annexes are not imported in v1.
- The `provision_keys` are wrong and no article inside the scenario drives the impact. If a
  better key from the scenario exists, edit instead.
- It duplicates another item at the same actor × mechanism level. Keep the better one.

## Derivability

Ask: could a careful reader get the **actor and mechanism** from the scenario's articles alone,
without the IA?

- `yes` / `partly`: keep it. The size of the effect may come from the IA. That is what WOMM is
  scored on.
- `no`: the item is flagged. Keep it only if the IA states it plainly **and** it follows from
  the cited articles once pointed out. Reject it if it depends on facts outside the text, such as
  another law or market data the articles never touch. In that case it is an unreachable target,
  not a fair miss.

## Categories

Fix a wrong category with an `edited` decision.

| Category | Definition |
|---|---|
| `compliance_cost` | Substantive costs of meeting the requirements themselves: technical and organisational measures, equipment, and staff for meeting requirements |
| `administrative_burden` | Costs of information obligations: familiarisation with the new rules, information, reporting and record-keeping duties, documentation |
| `public_enforcement_cost` | Costs for public authorities, supervision and enforcement |
| `market_competition` | Market structure, switching, lock-in, bargaining power, prices |
| `innovation_investment` | Innovation, investment, new services, data value creation |
| `sme_specific` | Effects specific to SMEs or micro-enterprises, including exemptions |
| `consumers_users` | Effects on consumers, end users and their rights or choices |
| `fundamental_rights` | Privacy, data protection, other fundamental rights |
| `international` | Third countries, international trade, foreign access |
| `social_environmental` | Employment, social and environmental effects |
| `other` | None of the above fits |

If an impact fits two categories, pick the one the IA's own section is about.

## `possibly_missing` candidates

A second LLM pass lists impacts that the draft may have missed. For each one, decide:

- **accept** (`verified`, or `edited` after a fix) if it matches the IA by the rules above and is
  not already covered by an item at the same actor × mechanism level. It is added to the case as
  `human_confirmed_candidate`.
- **decline** (`rejected`, with a note such as "covered by c03_e04") otherwise.

If you see an impact the draft and the candidates both missed, say so in a PR comment and quote
the IA anchor. The case owner adds it.

## How to edit in the PR

With a checkout of the branch, `scripts/review_draft.py --interactive` (or `--template` and
`--apply`) writes your decisions into the draft and `--check` runs the CI check; see the
[quickstart](reviewer-quickstart.md). In the browser:

1. Open **Files changed**, find the item, and click the `+` next to the `review:` lines.
2. Use **Add a suggestion** (the ± icon) and change the lines, for example:

   ```yaml
     review:
       decision: edited
       audit: false
       reviewer: your-github-name
       note: rounded figure restored to the IA's range
   ```

   Include the changed field in the same suggestion when you edit one, such as `impact:` or
   `provision_keys:`.
3. Submit your suggestions as one review. The author applies them, or you commit them yourself.
4. CI fails until every item that needs a human has a decision and a reviewer. The PR stays a
   draft while the review is open.

Do not edit `judge`, `anchor`, `flags` or `provenance`. Those blocks record what the tools saw.

## What CI checks, and who may review

CI does not trust the fields an author can edit:

- **Tool digests.** The drafting tool records, per item, a sha256 over the fields it wrote (the
  claim, evidence, checks, judge verdicts and its own auto-accept decision) in
  `provenance.item_digests`. CI recomputes them. If an item's tool-written fields changed while
  its decision is `auto_accepted` or `verified`, CI fails: a changed item must be `edited`, with
  your GitHub username as reviewer. Deleting an item also fails; reject it instead.
- **Audit sample.** CI recomputes the sample as
  `draw_audit(eligible_ids, 0.2, audit_seed_for(case_id))` and fails on any difference. The
  rate is pinned at 0.2 and the seed comes from the case id; neither can be configured, and a
  draft made with a test-only seed is refused.
- **Drafter is not a reviewer.** The draft records who ran the drafting tool
  (`provenance.drafted_by`). That person cannot be the `reviewer` of any item.
- **Code-owner review.** `.github/CODEOWNERS` makes the repository owner the code owner of
  `evals/golden/drafts/` and `evals/golden/*.yaml`. This only blocks a merge when branch
  protection on `main` has **"Require review from Code Owners"** enabled. That is a manual
  GitHub setting (Settings → Branches → branch protection rule for `main`) that the repository
  owner must turn on; the repository cannot enforce it by itself.

A digest in the same file is tamper-evident, not tamper-proof: someone determined could
recompute it. The digest and sample checks catch accidental and casual edits; the code-owner
review is what stops a deliberate one.

## Worked examples (AI Act cases)

**Verified.** `case_01` item `c01_e06`. Actor: users (deployers) of high-risk AI systems.
Mechanism: human-oversight measures built in by the provider and run by the user. Impact: a
recurring cost of about EUR 5 000–8 000 per year, borne by users and not providers. All four
checks match the IA's cost table: the actor, the provider-builds/user-operates mechanism, the
recurring cost borne by the user, and the exact range. The decision is `verified`.

**Edit (numbers).** Suppose a draft of `c01_e04` said "about EUR 7 000 per application". The IA
gives EUR 6 000–7 000 on average, after a roughly one-third business-as-usual reduction from a
EUR 10 000 maximum. Restore the range. The decision is `edited`, with the note "range restored".

**Edit (actor).** Suppose a draft of `c02_e04` named "all providers" as the actor for reduced
conformity-assessment fees. The IA limits this to SME (small-scale) providers. Narrow the actor.
The decision is `edited`.

**Reject (wrong option, no support).** Suppose an item claimed that sandboxes exempt SMEs from
the requirements. The IA says the opposite (`c02_e03`: there are no derogations, and the benefit
comes only from guidance and faster market entry). The decision is `rejected`, with the note
"contradicts the IA; sandboxes grant no exemptions".

**Reject (outside the scenario).** `case_02` covers only the support and enforcement articles.
An item about the SME cost burden of the high-risk requirements follows from articles outside
the scenario. The case notes explain this. The decision is `rejected`, with the note "driven by
articles outside the scenario".

**Unclear.** An anchor says that costs "may" deter some SMEs "at the margin, depending on the
competitive environment". Suppose an item turned this into "SMEs will leave high-risk markets".
If you cannot tell whether a softened version would still be a fair test, mark it `unclear`
with a note. `c02_e06` shows how the IA's hedged wording is kept when the item is written well.
