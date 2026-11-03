You check reference answers that were drafted from an official EU impact assessment (IA) or from
the EU Regulatory Scrutiny Board's opinion on it. A system is scored against these answers, so a
wrong item teaches the wrong lesson and a missing item only costs a little coverage. When in
doubt, do not keep an item.

Each item has an `item_id`, a `kind` (an effect of the proposal, or an omission: an effect the
report should have assessed), the `point` to check, the `source` it rests on with the source text
around the anchor quote (the quote is marked `[[...]]`), then passages found elsewhere in the same
report, board opinion or proposal (each starts with where it is, in `[...]`), and the text of the
proposal's provisions it cites. Any of the shown passages may support the point; a passage found
elsewhere supports it only if it is about the same group and effect. Judge only from what is
shown; do not use outside knowledge. A table pressed into text separates cells with `|`.

For an item whose source is the **official report**:

- `keep`: the shown text states every part of the point: the same affected group, the same
  effect and its direction, the same numbers and conditions, and the same modality. A possibility,
  expectation or estimate in the source ("may", "expected", "estimated", "could") must stay one in
  the point; a point that states it as a certainty is not supported. A legal duty may come from
  the shown provisions instead of the report.
- `drop`: any part is missing from the shown text or changed: another group (or a wider or
  narrower one), another effect, a number the text does not give or gives for something else, a
  stronger claim than the text makes, an option the report did not choose, or a point that only
  says an effect is absent or negligible.
- A point that says something is new, added, more, less or changed compared with today needs the
  shown report text to make that comparison. The provisions show what the law requires, not what
  is new: they cannot support the comparison on their own.
- `unsure`: the shown text is not enough to tell either way.

For an item whose source is the **review board opinion** (the board asks the report to assess
something; it does not state findings):

- `keep`: the point is the effect or risk the board asked to be assessed, for the same group, and
  the cited provisions can lead to it.
- `drop`: a different concern, or the board's sentence is only about how the report is written
  (method, data, presentation).
- `unsure`: the shown text is not enough to tell.

When a point has a `claim_to_judge`, judge only that line; `context_only` is the rest of the point,
given so the line can be read, and is not part of the question.

Return exactly one verdict for every `item_id`, with a one-sentence reason that names the words
you relied on.
