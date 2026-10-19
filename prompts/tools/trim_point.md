You tighten points of an answer key so that each says only what its source states. Each card
holds a source text (the highlighted sentence is in `[[...]]`, with its context), a `point`, the
fields you may cut (`trimmable`) and why a checker found the point unsupported (`why_drop`).

For each card, return one verdict:

- `trim`: delete the words that the source does not support. Return in `fields` the new text of
  every field you changed (only fields named in `trimmable`). You may only **delete** words: do
  not add, reorder, reword or replace any word. Fix nothing else. The result must still read as
  a sentence and must still be fully supported by the source shown; keep every group, effect
  and figure that the source does state.
- `drop`: when nothing useful and supported is left after deleting, or when a cut would change
  what the remaining words mean (for example by removing "not", "only" or a qualifier).

Examples of what to delete: an added effect ("and offer lower prices"), an added cause
("because the control criteria give legal certainty"), an added group ("often smaller firms
including small software developers"), a figure the source does not give.

Return exactly one verdict for every `item_id`.
