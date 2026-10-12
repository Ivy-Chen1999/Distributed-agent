# Every check, in one page

The rule behind all of them: **the AI does everything it can; people check only where an AI
cannot be trusted to check another AI.** That leaves three human checks; everything else is
automatic.

## Human checks

| Check | What people do | Why a person | Who, how long | What the result decides |
|---|---|---|---|---|
| **Reference check** | Read a highlighted sentence from the impact assessment and say whether it backs up an answer-key point: keep, drop or can't tell. | The answer key was written and judged by AI; checking it with another AI would repeat the same blind spots. | Two classmates, 20 points each (about 15 minutes); 10 more each only if the first round finds an error. | Whether the AI-made answer keys are used as they are. The AI may be wrong on at most 10% of the points. |
| **Companion check** | The same task for the points drafted from the RSB opinions. | A new kind of point the first check did not cover. | The same two classmates, every such point, split between them. | Whether those points are added to the cases. |
| **Grader calibration** | For 30 pairs, say whether WOMM's report covers an effect from the official list. | The grader that scores WOMM is an AI; it is trusted only if it judges like people. | The same two classmates, about 20 minutes each, after the cases are published. | The grader is used for promotions only if the two people agree with each other on at least 80% and the grader agrees with them on at least 85%. |
| **Sealed-case spot check** | The owner checks 3 points of each of the 8 sealed cases. | The sealed answer keys are made by the same AI tools; they cannot be shown to anyone else. | The owner, about 24 points, on a page on their own machine. | A disagreement on any point sends that case to a full check of every point. |

All of these are **blind**: the page never shows what the AI decided. The sample is random and
spread evenly over the three ways a point was decided (kept by the judges, kept by the tie-break,
dropped by the tie-break), so the result says how often the whole pipeline is wrong, not only on
the hard cases.

## Automatic checks

| Check | What it does |
|---|---|
| Quote check | Every answer-key point must quote its source word for word; a program finds the quote in the official text. |
| Three AI judges | Check each drafted point: faithful to the quote, follows from the law, right kind of effect. |
| AI tie-break | Settles the points the judges disagreed on. |
| Self-check of each case | Each case's own answer, scored as if it were WOMM's report, must get full marks. |
| Noise run | Runs the same WOMM version 6 times to measure how much scores move by chance, so a promotion needs a gain bigger than that. |
| Promotion gate | A new version is adopted only if it beats the current one on the sealed cases, under rules signed on 2026-10-12 before any comparison; at most 6 comparisons in total. |
| Cost reference check | The 11 cost figures from the AI Act's impact assessment are checked against the original text (11 of 11 matched). |
| Change monitor (R37) | Scores how well WOMM describes the changes between the AI Act proposal and the final law; shown, never used to decide. |

## The API key

Formal runs use an OpenAI model. The account is opened by a classmate with a hard monthly limit
(USD 10-20) and a key that works for this project only. The key is handed over through a
one-time secret link, never in chat or email, kept only in the owner's local `.env` file (never in
the repository or logs) and revoked after the demo. Before any sealed case is sent to the
provider, its data-retention terms are checked.
