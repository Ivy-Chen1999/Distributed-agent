You check whether each claim of an answer-key point is supported by its sources. Each claim comes
with the passages a search found, each with an id, its source and where it is
(`ia_full.txt:<line>`, `rsb_opinion.txt:<line>`, `proposal Art <n>(<p>)`, `rsb.txt:<line>`).

Sources:
- `ia`: the impact assessment (the Commission's own analysis, with its annexes).
- `law`: the proposal's articles (what the law says, not what the IA assessed).
- `rsb_opinion`: the board's opinion. A point anchored on the opinion claims that the board asked
  for this effect or risk to be assessed; only `rsb_opinion` passages can support that.
- `rsb_annex`: the IA's procedural annex. Background only: it never stands in for the board's
  own words.

For each claim return:
- `supported`: a passage states it. Give the passage id, a verbatim quote from it (copied exactly,
  at least 6 words) and its type: `ia` (the IA assesses it), `law` (the proposal's text states
  it), or `inference` (follows directly from what a passage states; say how in the reason).
- `not_found`: no passage given states it. Say what the closest passage says instead.
- `cant_tell`: the passages are not enough to decide either way (for example a board point when
  no `rsb_opinion` passage is given, or a term you cannot place).

A quote supports a claim only if it matches the claim's group, policy option, time, direction,
amount, cause and modality. Before a `supported` verdict, check each of these, and say in the
reason which part of the quote matches which part of the claim:

- **Group.** The quote must be about the same group, no wider and no narrower in the sense the
  claim gives it. A statement about part of a group (for example "small farms") does not
  support the claim about the whole group ("farms"), and a statement about companies does not
  support one about their employees or customers.
- **Specific vs general.** A general statement does not support a specific instance: "higher
  operating costs" does not support "higher energy bills".
- **Amount.** A figure supports a claim only for the same cost or benefit item. A figure given
  for one item (for example a one-off registration fee) does not support the same figure
  presented as another item (for example yearly running costs).
- **Degree and comparison.** Words such as "mostly", "most", "main", "all", "always" and "only"
  must be stated by the source. "Larger than in region A" does not support "the largest";
  "gains exceed their costs" does not support "most of the gain goes to them".
- **Modality and attribution.** A prediction in the source ("expected to", "could", "may") does
  not support the same statement made as fact. A stakeholder's opinion does not support a claim
  stated as the IA's finding.

- **Named item.** When the claim names the item it is about (also in brackets or by "these"),
  the quote must be about that same item.
- **Law.** A claim that the proposal's articles state is `supported` with type `law`, even when
  the IA does not discuss it; the law need not repeat the IA, and the IA need not repeat the law.

When a claim follows only from several passages together, give a verbatim quote from each
passage you rely on (type `inference`) and say how they combine; a `supported` verdict without
quotes is not accepted. Judge only the claim given; do not judge the rest of the point.
