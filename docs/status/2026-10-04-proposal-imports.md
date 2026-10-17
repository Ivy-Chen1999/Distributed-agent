# Proposal imports — 2026-10-04

Plan: `docs/plans/2026-10-04-001-feat-golden-case-expansion-plan.md`, units U1 and U2.
Branch `feat/golden-import`.

## What changed

- **U1.** `GoldenCase` has `fixture` (default `ai_act`, resolved to `data/fixtures/<fixture>`) and
  `split` (`train` | `val` | `holdout`). `split` defaults to `train` only for `ai_act` (the two
  existing cases); a case for any other fixture must state it, or it fails to load. The eval
  runner checks and runs each case against its own fixture. The YAML loader refuses
  `split: holdout` and reads only top-level `evals/golden/case_*.yaml`, so `drafts/` is never
  scored. `womm eval --split train|val`; an empty selection exits 2.
- **U2.** `scripts/import_proposal.py <COM CELEX>` imports a whole proposal into
  `data/fixtures/<regulation_id>/`: every article as a provision keyed
  `<regulation_id>/proposal/art/<n>`, the memorandum stripped by topic pattern
  (`src/womm/data/memorandum.py`) and checked by the leak guard. The main document is resolved
  from Cellar's HTTP 300 listing when needed (`src/womm/data/cellar.py`) and pinned by sha256.
  Per-proposal settings are in `import.yaml`. A failed import writes nothing.
- **Parser hardening** (`src/womm/data/parse_proposal.py`), after a byte-for-byte
  characterization of the COM(2021) 206 parse, which is unchanged: `Titrearticle*` variants,
  `ChapterTitle`, headings split mid-word, titles in the next paragraph, chapter headings in the
  article class (CRA), `li Point0` numbered paragraphs (EHDS), bullet-numbered memorandum
  subsections, and `check_article_sequence` (zero articles, gaps and duplicates fail, naming the
  CELEX).
- **Sentence-level redaction** (user decision, 2026-10-04). When a memorandum mentions the IA
  outside the stripped sections, `import.yaml` lists `redactions`: each entry has a `section`
  (full heading or number of a kept section), a `sentence` (exact text or a unique prefix) and a
  `reason`. `womm.data.memorandum.redact_sentences` removes exactly that sentence and fails,
  naming the CELEX, when an entry matches no sentence or more than one, or when its section was
  stripped, so stale entries cannot pass. Sentences end at `.`, `!` or `?`, but not after an
  abbreviation ("e.g.", "i.e.", "cf.", "Art.", "No.", ...). The leak guard then runs on the
  redacted text and must pass. Each memorandum source records only the count per section,
  e.g. `redactions: ["redacted: 2 sentences in 1.1. Reasons for ..."]`, next to
  `stripped_sections`. Neither the sentence nor the reason is written to `sources.json`: a
  reason can restate an IA conclusion, so reasons stay in `import.yaml` (which the agents do not
  load) and in the import log.
- **Leak guard.** Text is normalised before matching: soft hyphens (U+00AD) deleted, hyphens
  and dashes read as spaces, whitespace collapsed, case folded. Markers are regexes: "impact
  assessment", "impact analysis", `\bias?\b` ("IA", "IAs"), "preferred option", "preferred
  policy option", "Regulatory Scrutiny Board", `\bswd\s*\(`, `\bsec\s*\(\s*\d{4}\s*\)`, "staff
  working document", "public consultation", "problem definition". The same check runs on
  scenario descriptions in `import.yaml` (and so in `scenarios.yaml`), with no allow-list.
- **Allow-list, section-scoped.** Each `leak_allow` entry is `{section, text}`: the phrase is
  masked only in that kept section and must occur there exactly once. It must contain a marker
  and a qualifying word beyond it; an entry that is only a marker plus articles, determiners,
  prepositions or a plural "s" ("the impact assessment", "IAs") fails, as does a stale entry.
- **No IA reference in public scenarios.** Imported fixtures' `scenarios.yaml` carries no
  `ia_reference` (the golden case names its IA); `import.yaml` scenarios refuse the field and
  `load_fixture` rejects it in any fixture that has an `import.yaml`.
- **IA identifiers, local only.** The IA's CELEX and publication date and the RSB reference are
  recorded for every imported proposal, but only in the gitignored
  `evals/private/ia_index.yaml` (`womm.data.ia_index`), keyed by fixture id. The importer writes
  it from `--ia-celex/--ia-date/--rsb-ref`, refuses an `import.yaml` that holds these fields,
  and the committed-fixture test asserts that no `import.yaml` names an IA or RSB document.
  Tests use a synthetic sample (`tests/fixtures/ia_index_sample.yaml`).
- **Parser fix.** COM(2021) 762 puts the last paragraph of "Choice of the instrument" inside the
  number span of the next heading. The last child span is now the number and the text before it
  goes back to the previous section.
- **Cellar `text/html` fallback.** Since 2025 Cellar registers the Commission's XHTML
  manifestation as `text/html`: an `application/xhtml+xml` request answers 404 (CELEX resource)
  or 406 (item). `cellar.fetch_document` retries with `text/html` on 404/406 only. On re-import
  it reads the cached body whose sha256 matches the pin in `downloads.json`, else the one cached
  last, rather than the XHTML cache entry blindly.

## Import results

23 committed fixtures (`scenarios: []`; scenarios come with the golden cases in U4/U5). Every
memorandum strips 2.3 Proportionality, section 3 with all its subsections (ex-post evaluations,
consultations, expertise, IA, regulatory fitness, fundamental rights, as present) and 4
Budgetary implications. "3.1–3.n" gives the number of section-3 subsections. All 23 were
re-imported after the leak-guard normalisation and the new markers: no new hits, no new
redactions or allow entries; only the `redactions` records in `sources.json` changed (reasons
replaced by counts).

| CELEX | Fixture | Articles | Stripped | Redacted (reasons) | Allow-listed | Result |
|---|---|---|---|---|---|---|
| 52022PC0068 | `data_act` | 42 | 2.3; 3, 3.1–3.6; 4 | 0 | – | clean |
| 52022PC0454 | `cra` | 57 | 2.3; 3, 3.1–3.5; 4 | 0 | – | clean |
| 52020PC0767 | `dga` | 35 | 2.3; 3, 3.1–3.4; 4 | 0 | – | clean |
| 52022PC0495 | `pld` | 20 | 2.3; 3, 3.1–3.5; 4 | 0 | – | clean |
| 52021PC0731 | `political_ads` | 20 | 2.3; 3, 3.1–3.5; 4 | 0 | – | clean |
| 52022PC0720 | `interoperable_europe` | 22 | 2.3; 3, 3.1–3.6; 4 | 0 | – | clean |
| 52021PC0346 | `gpsr` | 47 | 2.3; 3, 3.1–3.6; 4 | 0 | – | clean |
| 52023PC0094 | `gigabit` | 18 | 2.3; 3, 3.1–3.6; 4 | 0 | – | clean |
| 52020PC0825 | `dsa` | 74 | 2.3; 3, 3.1–3.6; 4 | 1 (1.3: IA problem finding on regulatory gaps) | – | clean after redaction |
| 52020PC0842 | `dma` | 39 | 2.3; 3, 3.1–3.4; 4 | 1 (5.1: policy option from the inception IA) | – | clean after redaction |
| 52022PC0197 | `ehds` | 72 | 2.3; 3, 3.1–3.6; 4 | 2 (1.1: IA finding on voluntary cross-border rules; 2.2: IA's evaluation result on soft instruments) | – | clean after redaction |
| 52021PC0281 | `eidas` | 2 | 2.3; 3, 3.1–3.6; 4 | 1 (5.2: links an article to measures assessed in the IA) | – | clean after redaction; amending act, so Article 1 holds all amendments |
| 52021PC0202 | `machinery` | 52 | 2.3; 3, 3.1–3.4; 4 | 1 (1.1: attributes the problem list to the IA report) | – | clean after redaction |
| 52020PC0798 | `batteries` | 79 | 2.3; 3, 3.1–3.6; 4 | 2 (2.1: impact-analysis conclusion on predominant objectives, found by hand, now also caught by the "impact analysis" marker; 2.4: IA conclusion on the choice of instrument) | 5.3: "results of a dedicated impact assessment" (a future IA for the carbon-footprint thresholds, not this proposal's IA) | clean after redaction |
| 52021PC0762 | `platform_work` | 24 | 2.3; 3, 3.1–3.5; 4 | 1 (5.1: cites the IA report as the source of the monitoring indicators) | – | clean after redaction (and the parser fix) |
| 52025PC0335 | `space_act` | 119 | 2.3; 3, 3.1–3.6; 4 | 0 | – | clean |
| 52026PC0011 | `cybersecurity_act_2` | 122 | 2.3; 3, 3.1–3.5; 4 | 1 (1.1: attributes the problem list to the IA's problem analysis) | – | clean after redaction |
| 52026PC0504 | `chips_act_2` | 60 | 2.3; 3, 3.1–3.5; 4 | 0 | – | clean; section 5 is titled "Other aspects", not "Other elements", so its source id is `section_5` |
| 52026PC0231 | `multimodal_booking` | 20 | 2.3; 3, 3.1–3.6; 4 | 1 (1.3: cites the accompanying IA document's climate-consistency finding) | – | clean after redaction |
| 52026PC0992 | `skills_portability` | 17 | 2.3; 3, 3.1–3.6; 4 | 0 | – | clean |
| 52025PC0747 | `drug_precursors` | 45 | 2.3; 3, 3.1–3.6; 4 | 1 (1.2: the IA's net cost-saving conclusion) | – | clean after redaction |
| 52026PC0599 | `affordable_housing` | 17 | 2.3; 3, 3.1–3.5; 4, 4.1–4.2 | 0 | – | clean |
| 52026PC0990 | `tcn_qualifications` | 42 | 2.3; 3, 3.1–3.6; 4 | 3 (1.1: IA baseline cost estimate from its survey; 2.2: consultation result; 2.2: the IA's chosen option and monetised savings) | – | clean after redaction |

The AI Act fixture (`ai_act`, COM(2021) 206) predates `import.yaml` and is not in the table.
The redaction reasons above summarise `import.yaml`; they are not in any `sources.json`.

### Attempts not imported

| CELEX | Reason |
|---|---|
| 52022PC0046 (Chips Act) | No IA: the memorandum states the proposal "is not accompanied by a formal impact assessment" (a staff working document followed later). Skipped. The leak-guard hit was the false positive "environmental impact assessment"; a section-scoped allow entry covers that case but was not needed. |
| 52023PC0360 (FIDA) | Article numbering jumps to 17 where 16 was expected. Not investigated; enough clean imports without it. |
| 52022PC0142 (ESPR) | Article 20 heading is marked `Normal`, not `Titrearticle`. Not fixed (needs a heading heuristic for plain paragraphs, not small). |
| 52022PC0496 (AILD) | Memorandum not recognised: no proportionality heading, and non-standard numbering ("1. RESULTS OF ..."). Not fixed (not small). |

12 further proposals from 2025–2026 were tried and not imported: 9 fail the article-sequence
check (gaps, duplicates or `28a`-style inserted articles), 2 have a non-standard memorandum (no
proportionality or budget heading), and 1, an amending act with 4 articles, imported cleanly but
was dropped as too small to carry cases.

## Hand inspection

Kept memoranda were scanned sentence by sentence for IA conclusions the markers miss (terms:
option, consultation, survey, estimate, cost, benefit, million/billion/EUR, "evaluation
shows/concluded", assessment, and for the 2025–2026 fixtures also "staff working", "found",
"shows", "according to", "identified", "expected to") and the hits read by hand.

- Redacted: batteries 2.1 "The impact analysis of the proposed measures demonstrates ..." (an IA
  conclusion under another name); the 6 redacted sentences in the 2025–2026 fixtures listed
  above.
- Kept, borderline, for review: political_ads 2.2 says incremental cross-border compliance costs
  "would be removed" (a subsidiarity argument, not a quantified IA result) and 1.1 mentions
  concerns raised "during the consultation"; machinery 1.1 lists problems with some
  consultation remarks (the redacted lead sentence attributed the list to the IA; the list itself
  is the proposal's own problem statement); pld and ehds quote ex-post **evaluation** findings,
  which the user decision keeps; platform_work 1.1 and dma 1.1 give external market statistics
  (problem context); multimodal_booking 1.1 cites "an analysis of 100 representative routes"
  (problem context, unattributed); chips_act_2 1.1 quotes the ex-post evaluation of the first
  Chips Act (kept under the evaluation rule) and says the objectives were "informed by an
  analysis of these challenges"; cybersecurity_act_2 and drug_precursors state qualitative cost
  reductions without figures.
- Earlier: data_act, cra and dga had no misses; CRA 1.1 keeps an external cybercrime cost estimate.

## Open

- eIDAS is an amending act with 2 articles; golden cases on it will cite Article 1 only.
- FIDA, ESPR and AILD remain unimported (parser work), as do 11 of the 2025–2026 proposals
  (article numbering with inserted or duplicated articles, non-standard memoranda).
