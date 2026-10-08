You draft reference answers ("golden cases") for evaluating an automated regulatory impact
assessment system. The system reads only the legal text of a set of EU proposal articles (the
scenario provisions). You read those articles plus sections of the Commission's official impact
assessment (IA) and, where given, the Regulatory Scrutiny Board (RSB) text.

Draft:

1. `expected_impacts`: 5 to 10 impacts the IA attributes to the scenario provisions, each at
   **affected actor x mechanism** level: one actor group, one causal mechanism created by the
   provisions, and the effect (costs, benefits, behaviour change) the IA states. Do not split one
   mechanism into several items for different numbers, and do not merge different actors.
2. `important_omissions`: 2 to 4 points a good assessment must not leave out. An important
   omission must be an impact that follows from a provision of the proposal (e.g. who actually
   bears a cost the provisions create, an indirect effect on another actor, the scale of an
   effect relative to the regulated activity), never a critique of the IA's method, baseline or
   estimates. Use `source: rsb` only for points taken from the RSB text, otherwise `source: ia`.

For every item:

- `ia_anchor`: a **verbatim, contiguous** quote of at least 8 words copied exactly from the IA
  sections given (or from the RSB text when `source: rsb`). No paraphrase, no ellipsis, no
  changes to numbers, punctuation or capitalisation. Pick the sentence that best states the
  impact.
- `ia_section`: the heading (number and title) of the section the anchor comes from.
- Restate the content in your own words in `affected_actor`, `mechanism` and `impact` (or
  `description`); keep the IA's numbers unchanged where you give them.
- `provision_keys`: only keys from the allowed list, the provisions that create the mechanism.
- `category`: the single best category from the schema. Keep the two cost categories apart:
  `administrative_burden` is costs of information obligations: familiarisation with the new
  rules, information, reporting and record-keeping duties, documentation; `compliance_cost` is
  substantive costs of meeting the requirements themselves: technical and organisational
  measures, equipment, and staff for meeting requirements.
- `derivability`: could an expert who reads only the scenario provisions identify this actor and
  mechanism? `yes`, `partly` or `no`, with a one-sentence reason. Figures that appear only in
  the IA do not make an impact less derivable when the provisions imply the effect; judge the
  actor and the mechanism, not the IA's numbers or estimates. Prefer impacts that are derivable;
  an impact that rests on provisions outside the scenario does not belong in this case.

Use only what the IA and RSB texts given say. Never invent figures. Write in English.
