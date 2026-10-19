# Colleague data integration — 2026-10-02

Plan: `docs/plans/2026-10-02-001-feat-pipeline-data-import-plan.md` (v0 P6, R38 in
`docs/brainstorms/2026-09-28-womm-phased-requirements.md`). Branch `feat/pipeline-data-import`.

## What changed

- The colleague delivered the data and the methodology in
  [calderonsamuel/course-cs-project-fall-2026-data](https://github.com/calderonsamuel/course-cs-project-fall-2026-data).
  The fixture's article texts now come from that pipeline's provision units, pinned to commit
  `16b5807927be1ade3802db7c59c26f7ca338ebf2`. The pin is in `scripts/build_fixture.py`
  (`PIPELINE_COMMIT`), and the sha256 of each file is in `data/fixtures/ai_act/downloads.json`.
- `src/womm/data/parse_units.py` renders their unit tree into articles. Their container alignment
  checks `crosswalk.yaml`: all 15 article pairs agree.
- The memorandum, the IA stripping, the scenarios and the golden cases are unchanged. No agent
  code changed. This confirms the R1 promise: integrating the colleague's data needed no change
  to the agent layer.
- `--articles-from cellar` still rebuilds the previous fixture, byte for byte.

## Fidelity (the 19 fixture articles)

| Result | Articles |
|---|---|
| Byte-identical to the Cellar parse | 14 |
| Same words, whitespace differs | proposal Art 9, 11, 15, 43 |
| Differs | final Art 99 only: "35000000" vs "35 000 000". The Formex source has no separator |

Citation matching collapses whitespace, so none of these changes affect grounding. Any eval
comparison across this change should still pool at least 6 runs per case
(`docs/solutions/evaluation/single-run-scores-are-noise.md`).

## Deviation from the plan

The plan expected byte identity for every article except Art 99. Their proposal units merge
unnumbered subparagraphs into the paragraph's text, so the line breaks between them do not exist
upstream. Four proposal articles therefore change in whitespace only. Restoring the breaks would
mean guessing sentence boundaries, so we did not try.

## Upstream issues to raise with the colleague

1. `full_text` reorders text around points. When a unit has its own text both before and after
   its children, the children are appended after all of the text, and a ` […] ` marker is left at
   the gap. Examples: proposal Art 9(4), Art 43(1) and Art 5(2); final Art 22(3) and Annex XI
   section 1 point 2.
   - In Art 43(1) the second marker separates two text blocks and has no children at it.
   - WOMM does not use `full_text`. It rebuilds the text and inserts children at the first gap
     whose text ends with ":".
2. In the proposal, unnumbered subparagraphs are merged into the paragraph text. The final act has
   them as `subparagraph` units, so the two versions are inconsistent.
3. A stray backtick in the heading of final Art 1 ("Subject matter`").
4. Superscripts are flattened. Final Art 51(2) sets the systemic-risk threshold at 10^25
   floating point operations, but the provision unit and the obligation record read
   "greater than 1025". It is the only superscript outside footnote calls in the Official
   Journal and consolidated texts.
   - WOMM restores it with an explicit fixup in `scripts/build_corpus.py` (`KNOWN_FIXUPS`). The
     build fails if the phrase is no longer found.
5. The repository has no LICENSE file.
6. `docs/project-status.md` links to `../project-plan.md`, which is not in the repository.
7. Please tag the delivered commit (for example `v0-delivery`), so that a force push cannot remove
   the commit we pinned.

## Next

- v0: the R36 page-2 provision comparison view for the demo diff scenario.
- v1: `docs/plans/2026-10-02-002-feat-scoped-provision-retrieval-plan.md`. The rest of the
  colleague's methodology is staged in
  `docs/brainstorms/2026-10-02-next-stage-cost-and-methodology-requirements.md`.
