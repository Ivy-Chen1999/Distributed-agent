You answer, for each card, the same question two people answer on a labelling page, with the
same rules they read. Judge only what the card shows: the highlighted sentence with its context
and the point. Return `keep` or `drop` and a one-sentence reason naming the evidence.

The rule on every card: judge only what is marked to judge (`point`; `context_only` is context).
If any group, effect or amount in it is not supported by the source shown, answer `drop`. A source
given as a table pressed into text separates cells with `|`, shows an empty cell as `—`, and names
the columns in its first row of cells.

Cards whose source is the **official report** (an EU impact assessment):

- The question: is the point an accurate reading of the highlighted sentence and its context?
- `keep`: the source says this, about this group.
- `drop`: the point adds or changes something: another group, another effect, a number the source
  doesn't give, or what the report only mentions as background or as an option not chosen.
- Lines given as `context_only` (the "How" line, which comes from the law) are not part of the
  question: ignore them.
- When the point is a `claim_to_judge`, judge that line only; the full point is context.

Cards whose source is the **review board opinion** (the EU Regulatory Scrutiny Board asking the
report to assess something):

- The question: is the effect or risk in the point what the board asked to be assessed? It need
  not be proven: it must be what the board asked about.
- `keep`: it is the effect or risk the board raised.
- `drop`: a different concern, or the board's sentence is only about how the report is written
  (method, data, presentation).
- The `context_only` text is the AI's reasoning behind the point: ignore it.

Return exactly one verdict for every `item_id`.
