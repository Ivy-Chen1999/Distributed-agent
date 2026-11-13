# UI personas

Who the console is for, and which UI sections each person sees. Third draft, 13 November 2026, written with the data repository ([calderonsamuel/course-cs-project-fall-2026-data](https://github.com/calderonsamuel/course-cs-project-fall-2026-data), which supplies the corpus and the obligation records).

**Method.** The personas follow Alan Cooper's goal-directed design: each is one specific, believable person, described by goals rather than tasks. Goals come in three kinds:
- **Experience goals:** how they want to feel while using the product.
- **End goals:** what they want to get done.
- **Life goals:** who they want to be.

**They are provisional.** Cooper builds personas from field research (interviews and observation), and none has been done. They are hypotheses drawn from the project's sources (the AI Act text, the Dutch municipal association's material, the municipal algorithm registers) and from WOMM's scope. Each persona ends with what would prove it wrong, and section 9 lists how to check them. The people are fictional.

**What the personas showed.** The console is **two products**: one for people who study a law, and one for people who study WOMM. Section 2 describes the split, section 6 maps every UI section to it, and section 7 says what the console implements.

Related:
- [README.md](README.md#interfaces): the two interfaces as implemented (`web/src/model/roles.ts`).
- [api-contract.md](api-contract.md): the endpoints behind the Regulations and Act screens.

## 1. The personas at a glance

| | Persona | Interface | Role | Main question |
|---|---|---|---|---|
| P1 | **Joris Vermeulen**, policy adviser at the municipal association | The law | **Primary** | What will this law cost the people I represent, and what did the impact assessment miss? |
| P2 | **Sanne de Wit**, privacy and security officer at a small municipality | The law | Secondary | What do *we* have to do, by when? |
| P3 | **Mila Kovač**, tech-policy reporter | The law | Secondary | What changed, for whom, and what can I cite? |
| P4 | **Tomás Reyes**, developer on the project | The system | **Primary** | Why did the system produce this, and how do I fix it? |
| P5 | **Dr. Lena Brandt**, course examiner and evaluation researcher | The system | Secondary | Does distributing the work across agents do real work? |
| — | A citizen or small-business owner looking for legal advice | neither | Negative | WOMM is not for them (section 5). |

In Cooper's terms, each interface has **one primary persona**: if the interface works for them, the others are served by narrowing it.
- Joris is primary for the law: the Reader's view is Joris's, made read-only and plain, and the Implementer's view is Joris's, filtered to one role.
- Tomás is primary for the system: Lena uses a subset.

## 2. Two products in one console

| | The law | The system |
|---|---|---|
| Studies | a regulation and its changes | WOMM: agents, runs, evaluation |
| Personas | Joris (primary), Sanne, Mila | Tomás (primary), Lena |
| Enters through | Regulations, a pasted EUR-Lex link | Overview of runs |
| Unit of attention | an act, an article, an analysis | a run, an agent, a system version |
| Shows | changes, duties, dates, findings with sources | status, latency, cost, logs, scores, lineage |
| Never shows | run ids, system versions, pipeline status, event logs | — (everything is available) |
| Starting analyses | Joris; Sanne rarely; Mila never | constantly |

The two interfaces share data, so a finding in the law interface links to its run in the system interface, but they don't share screens. Today's console mixes them, which is why roles had to hide so much. The split also gives a natural security boundary: outside users only ever get the law interface, behind a read-only API token.

## 3. The law interface

### P1. Joris Vermeulen — Impact analyst (primary)

**Senior policy adviser at the Association of Netherlands Municipalities (VNG).** 47, a lawyer by training, has written implementation analyses for 12 years.

> "The Commission's impact assessment always looks fine. My job is to find what it left out, before it lands on 342 municipalities."

**Context.**
- Writes the association's implementation analysis of a new act. It takes weeks, runs through expert groups with about 60 municipalities, and ends as a report with recommendations and cost estimates.
- Also comments on proposals while negotiations are still open.
- Reads EUR-Lex daily and knows the legislative process well.
- Thinks about municipalities **in aggregate**, never about one municipality's systems.

**Goals.**
- *Experience:* feel in control of the evidence; nothing hidden behind a summary.
- *End:* a defensible list of the obligations that will cost municipalities most, which of them were added late (after the impact assessment), and the text behind each.
- *Life:* be the person ministries call because their numbers hold up.

**Behaviours.**
- Works across many sessions, comparing proposal → final text clause by clause.
- Starts analyses deliberately, compares them, exports to a spreadsheet.
- Doesn't take a model's word for anything: opens every source and argues with disagreements.

**Frustrations.**
- Impact assessments ignore implementation by local government.
- Tracking changes across versions by hand in Word.
- AI tools that sound confident and can't show where a claim comes from.

**Key scenario.** The final AI Act text is published. Joris opens proposal → adopted text on the map and filters to duties on public bodies. They see that Art. 27 (the fundamental rights impact assessment) is entirely new, start an assessment, then work through its disagreements and open questions for the report.

**Not interested in:** agent latency, tokens, system versions, router decisions. They care that the analysis is sound, not how WOMM produced it.

**What would prove this persona wrong:** association analysts won't start AI analyses themselves and only want finished, reviewed ones. Then they collapse into the Reader with more depth.

### P2. Sanne de Wit — Implementer

**Information security and privacy officer at the municipality of Terneuzen** (about 54,000 residents). 39, works part-time, and is the only person covering privacy, security and now AI.

> "Just tell me what I have to do, by when, and what to ask our suppliers."

**Context.**
- Not a lawyer.
- Has a list of 11 registered algorithms, most bought from suppliers (Centric, Visma, Microsoft).
- Learns about new rules from VNG newsletters, never from EUR-Lex directly.
- Has to explain them to a manager and a budget committee in plain language.

Sanne stands for anyone who has to comply with a law inside one organization, a company as much as a municipality. A company's compliance officer would differ in sector, not in goals.

**Goals.**
- *Experience:* feel that the task is bounded: a list, not an ocean.
- *End:* the three to five duties that apply to *their role* (deployer, public body), the dates, and what to ask each supplier.
- *Life:* not be the person blamed when the regulator or the local newspaper finds a gap.

**Behaviours.**
- Reads in short bursts and prints things for meetings.
- Always filters to "us": skips any duty that belongs to providers or authorities.
- Trusts VNG more than any tool.

**Frustrations.**
- Analyses written for lawyers, or for "municipalities in general".
- Being told something applies without being told *which of our systems*.
- Deadlines that move: the Omnibus changed the dates they'd planned around.

**Key scenario.** A VNG newsletter says the Omnibus moved the high-risk dates. Sanne opens the AI Act and picks "deployer" and "public body". They see 25 of their duties now start later and nothing new to do, and write a three-line note to their manager.

**Not interested in:** proposal history, how WOMM works, impacts on other actors.

**How Sanne differs from Joris:**

| | Joris | Sanne |
|---|---|---|
| Scope | all municipalities, all actors | one organization, one role |
| Timing | before and at adoption | after adoption |
| Purpose | evidence for a report | a to-do list |
| Screens | — | **the same as Joris's**, with different filters and defaults |

**What would prove this persona wrong:** implementers never use a tool like this directly and only read what their association publishes. Then Sanne is served through Joris, and the persona drops.

### P3. Mila Kovač — Reader

**Tech-policy reporter at a European news outlet.** 34, covers EU digital regulation, writes 2–3 pieces a week.

> "I don't need to understand your system. I need to know what changed, and I need a source I can link to."

**Context.** Works from a laptop, between calls and deadlines. Arrives from a EUR-Lex link, a press release or a colleague's message, often with fifteen minutes before a draft is due. Reads legal text when forced to and skims it. Has never heard of agents, runs or dossiers, and doesn't want to.

**Goals.**
- *Experience:* feel oriented within a minute; never feel stupid.
- *End:* answer "what does this change mean, and for whom?" with a citation they can publish.
- *Life:* be the reporter who gets regulation right, not the one corrected by a lawyer on social media.

**Behaviours.**
- Searches by the name people use ("the Omnibus", "AI Act"), not by number.
- Copies quotes into notes.
- Leaves the moment something looks like a developer tool.
- Comes back occasionally, months apart.

**Frustrations.**
- EUR-Lex gives the text but not the change.
- Law firm summaries are behind sign-up walls and disagree with each other.
- Words like "degraded" or "run_508497b5" make a tool feel untrustworthy rather than rigorous.

**Key scenario.** The Digital Omnibus is adopted. Mila pastes the EUR-Lex link and sees that the high-risk deadlines moved 16 months. They open the one finished analysis, copy two sourced findings, and close the tab. Five minutes, no account.

**Not interested in:** which agent said what, scores, costs per run, starting anything.

**Open question.** Mila may be Joris on a short visit, rather than a persona with needs of their own. If Mila never needs anything Joris doesn't, the Reader becomes a *mode* of the law interface (read-only, plain language, no run controls), not a separate set of screens.

**What would prove this persona wrong:** reporters say they'd never cite an AI-generated analysis, only the legal text itself.

## 4. The system interface

### P4. Tomás Reyes — Builder (primary)

**Developer on the project, maintaining the pipeline and agents.** 27.

> "When a finding looks wrong I need to see exactly which input, prompt and model produced it."

**Goals.**
- *Experience:* nothing hidden.
- *End:* find and fix what's broken (a bad alignment, an ungrounded quote, an expert failing), and ship a better system version.
- *Life:* build something that holds up when examiners and colleagues look closely.

**Behaviours.**
- Lives in event logs, settings, backend overrides and system versions.
- Starts runs constantly, on fake and real backends.
- Reads the law interface to check what users will see.

**Needs that exist nowhere yet.** A review mode for uncertain matches, aligner gaps and duties with no identified addressee. These were in the early mockups and were dropped when the Act screen moved into WOMM.

### P5. Dr. Lena Brandt — Evaluator

**Lecturer who examines the course project, and a researcher in AI evaluation.** 42.

> "I don't care whether the AI Act analysis is right. I care whether you can show *why* five agents beat one."

**Context.** Reads the project once in depth for assessment, then again at the demo. Knows multi-agent systems, evaluation methodology and statistical noise well, and has no particular interest in EU law.

**Goals.**
- *Experience:* feel that claims are measured, not asserted.
- *End:* judge whether distributing the work across agents does real work compared with a single agent, and whether the evaluation is sound (held-out data, noise, judge calibration).
- *Life:* hold students to research-grade standards.

**Behaviours.**
- Goes straight to the agent graph, the evolution lineage and the holdout decisions.
- Asks "what's the variance?"
- Distrusts single impressive examples.

**Frustrations.** Demos that show one lucky run. Metrics without spread. Evaluation data leaking into training.

**Key scenario.** Opens Evolution, checks that holdout decisions are sealed and the noise is reported, then compares a multi-agent run with the single-agent baseline on the same scenario. Finally opens the Act screen to see whether the AI Act lights up far more than the contrast act.

**Not interested in:** any particular regulation's content, plain-language summaries, roles in the law.

**How Lena differs from Joris:** Lena evaluates **WOMM**; Joris uses WOMM to understand **a law**. Their overlap in today's console (Overview, Pipeline) is shared machinery, not shared goals. Lena belongs with Tomás, and visits the law interface only for the contrast test.

## 5. Negative persona

**A citizen or small-business owner looking for legal advice:** "Does my shop's chatbot break the AI Act?" WOMM gives sourced analyses of regulatory change, not advice about one person's situation. Designing for them would push the interface toward reassurance and simplification, which would harm Joris, Sanne and Mila.

## 6. UI sections by persona

**Key:**
- **✓** visible.
- **Plain:** shown in the law interface's plain form: no run ids, versions, statuses or logs.
- **Filtered:** narrowed to the reader's role in the law.
- **–** not shown.
- **New:** the section doesn't exist yet.

Columns are the personas, grouped by interface.

### 6.1 Shell

| Section | Joris | Sanne | Mila | Tomás | Lena |
|---|---|---|---|---|---|
| Interface switch (the law / the system) | – | – | – | ✓ | ✓ |
| Sidebar: the regulation the user chose (law); the selected run (system) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Run status badge, clock | – | – | – | ✓ | ✓ |
| System-version chip | – | – | – | ✓ | ✓ |
| Header Run button, run picker | – | – | – | ✓ | ✓ |
| Theme toggle | ✓ | ✓ | ✓ | ✓ | ✓ |
| API token: full | – | – | – | ✓ | ✓ |
| API token: read-only (New) | ✓¹ | ✓ | ✓ | – | – |

¹ Joris starts analyses, so his token must also allow that. The read-only token is for Sanne and Mila when they're outside users.

### 6.2 The law interface

| Section | Joris | Sanne | Mila | Tomás | Lena |
|---|---|---|---|---|---|
| **Regulations:** search, pasted links, act cards | ✓ | ✓ | ✓ | ✓ | ✓ |
| Analyses list per act | ✓ | ✓ | ✓ | ✓ | – |
| Example entries (mock data) | – | – | – | ✓ (development only) | – |
| **Act:** comparison picker; inline, side by side and map | ✓ | ✓ | ✓ | ✓ | ✓ |
| Measures: changed text, moved dates, all duties | ✓ | ✓ | ✓ | ✓ | ✓ |
| Measures: findings, duties by actor in changed text | ✓ | Filtered | ✓ | ✓ | ✓ |
| "Your role in the law" picker | ✓ (optional) | ✓ (asked first) | ✓ | ✓ | – |
| Findings layer, driven by the analysis open (New: today always the latest run) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Start an assessment | ✓ | ✓ (rarely) | – | ✓ | – |
| **Analysis:** header naming what was analysed and when | Plain | Plain | Plain | ✓ | – |
| Incompleteness stated in words | ✓ | ✓ | ✓ | ✓ | – |
| Provision comparison | ✓ | ✓ | ✓ | ✓ | – |
| Impacts with evidence and "View in source" | ✓ | Filtered (New) | ✓ | ✓ | – |
| Confidence per finding, labelled "model confidence, not verified" | ✓ | ✓ | ✓ | ✓ | – |
| Provision keys (internal ids) | – | – | – | ✓ | – |
| Impact chains, disagreements, open questions | ✓ | ✓ | ✓ | ✓ | – |
| Costs: payer, effort, magnitude | ✓ | Filtered (New) | ✓ | ✓ | – |
| Export findings and duties to a table (New) | ✓ | – | – | ✓ | – |
| What to ask suppliers (New, needs Stage C) | – | ✓ | – | ✓ | – |

### 6.3 The system interface

| Section | Joris | Sanne | Mila | Tomás | Lena |
|---|---|---|---|---|---|
| Overview: latest run, KPIs, agent latency | – | – | – | ✓ | ✓ |
| Overview: recent runs, needs review | – | – | – | ✓ | ✓ |
| Run pipeline: agent graph, router decisions, live board, replay | – | – | – | ✓ | ✓ |
| Run detail: run ids, system version, event log, failed experts | – | – | – | ✓ | ✓ |
| Agents, Topology | – | – | – | ✓ | ✓ |
| Evolution: lineage, config diffs, holdout decisions, R37 | – | – | – | ✓ | ✓ |
| Multi-agent vs single-agent comparison (New) | – | – | – | ✓ | ✓ |
| Act vs contrast act comparison (New; needs the Interoperable Europe Act corpus) | – | – | – | ✓ | ✓ |
| Review mode: uncertain matches, aligner gaps, unassigned duties (New) | – | – | – | ✓ | – |
| Settings: backends, router mode, overrides | – | – | – | ✓ | – |

## 7. Status in the console

What sections 2 and 6 ask for, and what the console does today.

| Item | Status |
|---|---|
| Two interfaces, the law and the system, switched in the sidebar | Built. The system is the default while the console is a team tool. |
| Law interface without run machinery (status, versions, run ids, event log, header Run button) | Built |
| Run detail as a plain Analysis in the law, with incompleteness stated in words | Built |
| Sidebar card follows the regulation chosen in the law, the run in the system | Built |
| Provision keys only in the system; confidence labelled "model confidence" in the law | Built |
| Example entries (mock data) only in development builds | Built |
| Findings layer driven by the analysis the user opened | Built |
| Role in the law shared by Act and Analysis; costs filtered by payer | Built |
| Impacts narrowed to the role in the law | Built as a wording match on each finding's free-text affected actor, off by default. A proper filter needs an actor id on findings. |
| Separate Analysis headers for Joris (full) and Mila (plain) | Not built: the law is plain for everyone. Revisit if the Mila check (section 9) says the Reader is a persona, not a mode. |
| Lena's contrast test on the Act screen | Needs a corpus for the contrast act (the Interoperable Europe Act). |
| Export of findings and duties to a table | Not built |
| Read-only API token that only offers the law interface | Not built. Needed before outside users get access. |
| Review mode (uncertain matches, aligner gaps, unassigned duties) | Not built |
| Multi-agent vs single-agent comparison view | Not built |
| What to ask suppliers | Needs Stage C (organization data) |
| Ask WOMM (questions to a model about one analysis) | Removed: no persona's key scenario needs it, and every question is an LLM call |

## 8. Design rules

1. **Two interfaces, two primaries.** A section belongs to the law (Joris) or the system (Tomás), never both. A shared section is a sign it should be split.
2. **Start from the regulation in the law interface,** from runs in the system interface.
3. **Narrow, don't fork.** Sanne and Mila get Joris's screens with filters and a plainer header, not new screens.
4. **Every number opens its source.** Generated text sits next to the cited text and is labelled as generated. A confidence value never replaces the citation.
5. **Counts follow the user's role in the law,** never a fixed actor.
6. **The law interface never shows run machinery:** run ids, versions, statuses, logs, latency, cost per run.
7. **Read-only is enforced by the server for outside users,** not only hidden in the browser.
8. **Validation data appears only in the system interface,** and never in agent inputs.

## 9. How to check these personas

| Persona | Check | It's wrong if |
|---|---|---|
| Joris | Ask VNG how their AI Act implementation analysis was produced, and run the timed task on proposal → final text. | They won't start analyses themselves. |
| Sanne | Run the timed task on the Omnibus comparison with one municipal officer and one company compliance officer. | They only ever read their association's output. |
| Mila | Show the Regulations → Analysis path to one or two reporters. | They need nothing Joris doesn't. The Reader is then a mode, not a persona. |
| Lena | Walk the course examiner through Evolution and the planned multi-agent vs baseline view before the demo. | They need data the system interface doesn't show. |
| Tomás | Ask the WOMM maintainers which screens they open first when a run looks wrong. | Their first stop isn't the event log and run detail. |
