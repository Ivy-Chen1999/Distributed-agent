import { describe, expect, it } from 'vitest';
import { buildTrace, elapsedAt, liveClock, statusAt, totalsAt, usageFor } from './trace';
import { agentCards, decisionVMs, feedVM, latBars, logLines, nodeVM, runStatusLabel, selInfo, stripVM, type RunView } from './pipeline';
import { attention, kpis, runRows } from './overview';
import { actorGroup, chainVMs, disagreementVMs, findingSteps, groupImpacts, questionVMs, shortLabel, unresolvedIds } from './dossier';
import { highlightQuote, normalizeWithMap } from './quote';
import { articleRanges, regulationSub, regulationTitle, scenarioBlurb, scenarioHeadline, scenarioKind, scenarioName } from './scenario';
import { MAX_CELLS, diffWords, hasComparableChanges, provisionComparisons, sideOf, tokenize, type Segment } from './compare';
import { UNRESOLVED_ID, demoSources, degradedRun, sampleRun, scenarios, smeSources, syntheticEvents, system } from '../test/fixtures';
import type { RunDetail } from '../types';

const EX = ['legal', 'fiscal', 'stakeholder'];
const view = (run: RunDetail, clock: number, events = syntheticEvents()): RunView => {
  const trace = buildTrace({ events, experts: EX, run });
  return { trace, clock, run, system, scenario: scenarios[1], changesCount: 4 };
};

describe('trace and node status', () => {
  const run = sampleRun();
  const tr = buildTrace({ events: syntheticEvents(), experts: EX, run });

  it('maps API nodes onto console nodes with timings from event timestamps', () => {
    expect(tr.spans.planner).toMatchObject({ s: 0.4, e: 26.8 });
    expect(tr.spans.citation).toMatchObject({ s: 130.2, e: 132 });
    expect(tr.spans.dossier).toMatchObject({ s: 287.2, e: 287.5 });
    expect(tr.end).toBeCloseTo(287.5);
    expect(tr.experts).toEqual(EX);
  });

  it('derives the regulatory diff and board', () => {
    expect(tr.spans.diff).toEqual({ s: 0, e: 0.4 });
    expect(tr.spans.board).toEqual({ s: 130, e: 130 });
    expect(statusAt(tr, 'diff', 0.2)).toBe('running');
    expect(statusAt(tr, 'diff', 1)).toBe('done');
    expect(statusAt(tr, 'board', 129)).toBe('queued');
    expect(statusAt(tr, 'board', 130)).toBe('done');
  });

  it('reports queued, running and done at a given clock', () => {
    expect(statusAt(tr, 'legal', 10)).toBe('queued');
    expect(statusAt(tr, 'legal', 50)).toBe('running');
    expect(statusAt(tr, 'fiscal', 80)).toBe('done');
    expect(elapsedAt(tr, 'legal', 50)).toBeCloseTo(23);
    expect(elapsedAt(tr, 'legal', 300)).toBeCloseTo(103);
    expect(nodeVM('legal', view(run, 50)).stT).toBe('Running');
  });

  it('marks a failed expert from the failures payload and degrades the run view', () => {
    const r = degradedRun();
    const trace = buildTrace({ events: syntheticEvents({ workforce: true }), experts: EX, run: r });
    expect(trace.experts).toEqual(['legal', 'fiscal', 'stakeholder', 'workforce']);
    expect(statusAt(trace, 'workforce', 60)).toBe('running');
    expect(statusAt(trace, 'workforce', 90)).toBe('failed');
    expect(trace.spans.workforce.errorKind).toBe('timeout');
    const rv: RunView = { trace, clock: trace.end, run: r, system };
    expect(nodeVM('workforce', rv).stT).toBe('Failed · timeout');
    expect(runStatusLabel('failed', 'pipeline_failed')).toBe('Failed · pipeline');
    expect(runStatusLabel('failed', 'schema_invalid')).toBe('Failed · schema invalid');
    expect(runStatusLabel('failed', null)).toBe('Failed');
    const bars = latBars(rv);
    expect(bars.find((b) => b.id === 'workforce')?.meta).toBe('timeout · 1m 00s');
    const k = kpis(rv, 'Degraded', '#E0A020');
    expect(k[0].sub).toBe('Workforce expert timed out');
    expect(k[4].value).toBe('98%');
    const att = attention(rv, smeSources.changes);
    expect(att.find((a) => a.key === 'failed')).toMatchObject({ n: '1', sub: 'Workforce · timeout' });
    expect(att.find((a) => a.key === 'unresolved')?.n).toBe('1');
  });

  it('logs a no_data_in_scope expert as a skip, not as a degrading error', () => {
    const run = degradedRun();
    const trace = buildTrace({ events: syntheticEvents({ workforce: true, workforceKind: 'no_data_in_scope' }), experts: EX, run });
    const rv: RunView = { trace, clock: trace.end, run, system };
    const line = logLines(rv).find((l) => l.node === 'workforce');
    expect(line?.level).toBe('INFO');
    expect(line?.msg).toBe('skipped · no data within its scope (no call)');
    expect(line?.msg).not.toContain('degraded');
    expect(nodeVM('workforce', rv).stT).toBe('Skipped · no data in scope');
    // A real expert error still reads as degrading the run.
    const timeout = buildTrace({ events: syntheticEvents({ workforce: true }), experts: EX, run });
    const tl = logLines({ ...rv, trace: timeout, clock: timeout.end }).find((l) => l.node === 'workforce');
    expect(tl?.level).toBe('WARN');
    expect(tl?.msg).toMatch(/^error_kind=timeout after .* · run continues as degraded$/);
  });

  it('marks a node failed on a failed event, and skips nodes that never ran', () => {
    const events = syntheticEvents().slice(0, 4).concat([{ seq: 5, node: 'expert_legal', event: 'failed', payload: { error: 'boom' }, at: new Date(Date.parse(syntheticEvents()[3].at) + 1000).toISOString() }]);
    const trace = buildTrace({ events, experts: EX, run: { status: 'failed', failures: [] } });
    expect(statusAt(trace, 'legal', trace.end)).toBe('failed');
    expect(statusAt(trace, 'synthesis', trace.end)).toBe('skipped');
  });

  it('never lets the live clock fall behind the last received event', () => {
    expect(liveClock(tr, tr.t0! + 10_000)).toBeCloseTo(287.5);
    expect(liveClock(tr, tr.t0! + 400_000)).toBeCloseTo(400);
  });
});

describe('usage aggregation', () => {
  const run = sampleRun();
  it('sums tokens, cost and latency per agent', () => {
    expect(usageFor(run.usage, 'legal')).toMatchObject({ tin: 17146, tout: 13621, cost: 0.20479, model: 'claude-sonnet-5' });
    expect(usageFor(run.usage, 'planner')?.lat).toBeCloseTo(26.45, 1);
    expect(usageFor(run.usage, 'router')).toBeNull();
  });
  it('counts only finished LLM nodes at the clock', () => {
    const tr = buildTrace({ events: syntheticEvents(), experts: EX, run });
    expect(totalsAt(tr, run.usage, 0).tokens).toBe(0);
    expect(totalsAt(tr, run.usage, 72).tokens).toBe(4240 + 2366 + 17156 + 5877);
    const all = totalsAt(tr, run.usage, 300);
    expect(all.tokens).toBe(119_909);
    expect(all.cost).toBeCloseTo(0.784938, 5);
    expect(stripVM(view(run, 300)).map((s) => s.value)).toEqual(['4m 48s', '119.9k', '$0.78', '64/64']);
  });
});

describe('board feed badges', () => {
  it('shows count rows while live and real findings afterwards', () => {
    const run = sampleRun();
    const live = feedVM(view(run, 100), null, null);
    expect(live.items.map((i) => i.key)).toEqual(['count_fiscal']);
    expect(live.items[0].badge).toBe('Checking quote');
    const done = feedVM(view(run, 300), run.board, run.dossier);
    expect(done.total).toBe(44);
    expect(done.items).toHaveLength(44);
    expect(new Set(done.items.map((i) => i.badge))).toEqual(new Set(['Quote verified']));
  });
  it('flags a finding whose evidence stayed unresolved', () => {
    const run = degradedRun();
    const feed = feedVM(view(run, 300), run.board, run.dossier);
    const f = feed.items.find((i) => i.findingId === UNRESOLVED_ID);
    expect(f?.badge).toBe('Quote not found');
    expect(feed.items.filter((i) => i.badge === 'Quote not found')).toHaveLength(1);
    expect(unresolvedIds(run.dossier).has(UNRESOLVED_ID)).toBe(true);
    // Before the citation check has run, every quote is still being checked.
    expect(feedVM(view(run, 131), run.board, run.dossier).items.every((i) => i.badge === 'Checking quote')).toBe(true);
  });
});

describe('router, log, agents, inspector', () => {
  const run = sampleRun();
  it('shows pending decisions before the router finishes', () => {
    expect(decisionVMs(view(run, 10)).map((d) => d.decision)).toEqual(['pending', 'pending', 'pending']);
    expect(decisionVMs(view(run, 30)).map((d) => [d.decision, d.w])).toEqual([
      ['relevant', '100%'],
      ['relevant', '100%'],
      ['relevant', '100%'],
    ]);
  });
  it('renders the event log from events', () => {
    const lines = logLines(view(run, 300));
    const msgs = lines.map((l) => `${l.node}: ${l.msg}`);
    expect(msgs[0]).toMatch(/^run: started · scenario eval_sme_impacts/);
    expect(msgs).toContain('router: legal=relevant fiscal=relevant stakeholder=relevant · mode=shadow decider=stub');
    expect(msgs).toContain('citation: 64/64 quotes found verbatim');
    expect(msgs.some((m) => m.startsWith('synthesis: 25 impacts · 5 chains · 1 disagreement'))).toBe(true);
    expect(msgs[msgs.length - 1]).toBe('dossier: assembled · status=succeeded · 25 impacts');
    expect(logLines(view(run, 50)).some((l) => l.node === 'synthesis')).toBe(false);
  });
  it('builds agent cards and inspector facts from usage and system roles', () => {
    const cards = agentCards(view(run, 300));
    expect(cards.map((c) => c.id)).toEqual(['planner', 'router', 'legal', 'fiscal', 'stakeholder', 'synthesis']);
    expect(cards[2].stats).toEqual([
      { k: 'latency', v: '1m 43s' },
      { k: 'tokens', v: '30.8k' },
      { k: 'cost', v: '$0.20' },
    ]);
    expect(cards[2].tags).toEqual(['prompts/legal.md', '7cabd9f203ee1062', 'claude-sonnet-5']);
    const s = selInfo('legal', view(run, 300));
    expect(s.facts).toEqual(expect.arrayContaining([{ k: 'Prompt hash', v: '7cabd9f203ee1062' }, { k: 'Backend', v: 'claude_code' }]));
    expect(s.ins).toEqual(['router']);
    expect(s.outs).toEqual(['board']);
  });
});

describe('dossier view-models', () => {
  const run = sampleRun();
  const d = run.dossier!;
  it('groups impacts by provision area and by actor', () => {
    const area = groupImpacts(d, 'area', smeSources.changes);
    expect(area.map((g) => g.label)).toEqual(['Regulatory sandboxes', 'Sandbox personal data', 'SME measures', 'Penalties']);
    expect(area.reduce((a, g) => a + g.items.length, 0)).toBe(25);
    expect(area[0].items[0]).toMatchObject({ id: 'I1', key: 'Art 53', merged: 'merged from 2 findings' });
    const actor = groupImpacts(d, 'actor', smeSources.changes);
    expect(actor.reduce((a, g) => a + g.items.length, 0)).toBe(25);
    expect(actor.length).toBeLessThan(10);
  });
  it('builds the five provenance steps', () => {
    const f = d.impacts[0].findings[0];
    const steps = findingSteps(f, smeSources.changes);
    expect(steps.map((s) => s.k)).toEqual(['Regulatory change', 'Affected actor', 'Mechanism', 'Evidence quote', 'Evidence quote', 'Source']);
    expect(steps[0].v).toBe('ai_act/innovation/regulatory_sandboxes · Art 53 · added');
    expect(steps[3].quote?.sourceId).toBe('com2021_206/art_53');
    expect(steps[5].v).toBe('com2021_206/art_53');
  });
  it('builds chains, disagreements and questions', () => {
    const chains = chainVMs(d);
    expect(chains).toHaveLength(5);
    expect(chains[0].steps.map((s) => s.id)).toEqual(['I1', 'I2', 'I4', 'I5']);
    expect(chains[0].steps[0].short.length).toBeLessThanOrEqual(25);
    const dis = disagreementVMs(d, run.board, smeSources.changes);
    expect(dis[0].heading).toBe('Art 55 · SME measures');
    expect(dis[0].sides.map((s) => s.agentName)).toEqual(['Legal', 'Fiscal', 'Stakeholder']);
    const qs = questionVMs(degradedRun().dossier!);
    expect(qs).toHaveLength(7);
    expect(qs[6]).toMatchObject({ tag: 'Evidence unresolved', unresolved: true });
  });
  it('derives short labels and actor groups', () => {
    expect(shortLabel('Tiered fines')).toBe('Tiered fines');
    expect(shortLabel('Small-scale providers and start-ups gain a preferential right')).toBe('Small-scale providers…');
    expect(actorGroup('national competent authorities')).toBe('Authorities');
    expect(actorGroup('small-scale providers and start-ups')).toBe('Small providers');
    expect(actorGroup('providers and start-ups participating in AI regulatory sandboxes')).toBe('Small providers');
  });
});

describe('quote highlighting', () => {
  it('finds every sample quote in its source text', () => {
    const run = sampleRun();
    const byId = new Map(smeSources.sources.map((s) => [s.source_id, s.text]));
    let checked = 0;
    for (const f of run.board!) {
      for (const e of f.evidence) {
        const h = highlightQuote(byId.get(e.source_id)!, e.quote);
        expect(h.found, `${f.finding_id}: ${e.quote}`).toBe(true);
        checked++;
      }
    }
    expect(checked).toBe(64);
  });
  it('tolerates whitespace, case, quote style, dashes, hyphenation and elisions', () => {
    const text = 'Art 1.\n\nThe  provider shall   “ensure” compliance —\nincluding regu-\n   lation of fees.\nEnd.';
    const h = highlightQuote(text, 'the provider shall "ensure" compliance - including regulation of fees');
    expect(h.found).toBe(true);
    expect(h.before).toBe('Art 1.\n\n');
    expect(h.quote).toBe('The  provider shall   “ensure” compliance —\nincluding regu-\n   lation of fees');
    expect(h.after).toBe('.\nEnd.');
    const el = highlightQuote(text, 'The provider shall […] including regulation of fees');
    expect(el.found).toBe(true);
    expect(el.quote.startsWith('The')).toBe(true);
    expect(highlightQuote(text, 'something that is not there at all').found).toBe(false);
  });
  it('keeps an index map back into the original text', () => {
    const n = normalizeWithMap('A­b  C');
    expect(n.text).toBe('ab c');
    expect(n.map).toEqual([0, 2, 3, 5]);
  });
});

describe('scenario labels', () => {
  const sc = scenarios[1];
  it('derives names and regulation labels', () => {
    expect(scenarioName('eval_sme_impacts')).toBe('SME impacts');
    expect(scenarioName('demo_penalties_amended')).toBe('Penalties amended');
    expect(regulationTitle(sc)).toBe('EU AI Act · proposal');
    expect(regulationSub(sc, smeSources.changes)).toBe('COM(2021) 206 · Art 53–55, 71');
    expect(scenarioKind(sc)).toBe('Evaluation · IA §6.1.4');
    expect(scenarioHeadline(sc)).toBe('How are SMEs and start-ups affected?');
    expect(scenarioBlurb(sc, smeSources.changes, 3)).toBe(
      'Four provisions changed: regulatory sandboxes, sandbox personal data, SME measures and penalties. Three expert agents read them in parallel and posted to one shared board.',
    );
    expect(articleRanges(['53', '54', '71', '72'])).toBe('53, 54, 71, 72');
    expect(articleRanges(['71', '53', '55', '54'])).toBe('53–55, 71');
    expect(articleRanges(['12a', '13'])).toBe('12a, 13');
  });
  it('formats recent runs', () => {
    const run = sampleRun();
    const rows = runRows([{ run_id: run.run_id, scenario_id: run.scenario_id, status: 'succeeded', system_version: 'sv_x', duration_s: 287.5, impacts: 25, grounding: { passed: 64, total: 64 } }], scenarios);
    expect(rows[0]).toMatchObject({ id: run.run_id.slice(0, 12), name: 'SME impacts', status: 'Succeeded', impacts: '25', grounding: '64/64', time: '4m 48s' });
  });
});

describe('provision comparison (word diff)', () => {
  const join = (segs: Segment[], skip: 'add' | 'del') => segs.filter((x) => x.op !== skip).map((x) => x.text).join('');
  const roundTrip = (a: string, b: string) => {
    const segs = diffWords(a, b);
    expect(join(segs, 'add')).toBe(a);
    expect(join(segs, 'del')).toBe(b);
    // Adjacent segments never share an op, and no segment is empty.
    for (let i = 0; i < segs.length; i++) {
      expect(segs[i].text.length).toBeGreaterThan(0);
      if (i) expect(segs[i].op).not.toBe(segs[i - 1].op);
    }
    return segs;
  };

  it('tokenizes into words, whitespace runs and single punctuation, losslessly', () => {
    expect(tokenize('Art 5(1)(a),  shall\n apply.')).toEqual(['Art', ' ', '5', '(', '1', ')', '(', 'a', ')', ',', '  ', 'shall', '\n ', 'apply', '.']);
    expect(tokenize('')).toEqual([]);
    expect(tokenize('EUR 35 000 000 — or 7 %').join('')).toBe('EUR 35 000 000 — or 7 %');
    expect(tokenize('Behörde ändert')).toEqual(['Behörde', ' ', 'ändert']);
  });

  it('marks same, deleted and added words', () => {
    const segs = roundTrip('Member States shall lay down rules.', 'Member States shall lay down the rules.');
    expect(segs).toEqual([
      { op: 'same', text: 'Member States shall lay down ' },
      { op: 'add', text: 'the ' },
      { op: 'same', text: 'rules.' },
    ]);
    expect(roundTrip('provide small-scale providers with access', 'provide SMEs with access')).toEqual([
      { op: 'same', text: 'provide ' },
      { op: 'del', text: 'small-scale providers' },
      { op: 'add', text: 'SMEs' },
      { op: 'same', text: ' with access' },
    ]);
    expect(roundTrip('a b c', 'a c')).toEqual([
      { op: 'same', text: 'a ' },
      { op: 'del', text: 'b ' },
      { op: 'same', text: 'c' },
    ]);
  });

  it('folds a lone space between two changes into one replacement', () => {
    expect(roundTrip('fine of 30 000 000', 'fine of 35 000 000')).toEqual([
      { op: 'same', text: 'fine of ' },
      { op: 'del', text: '30' },
      { op: 'add', text: '35' },
      { op: 'same', text: ' 000 000' },
    ]);
    expect(roundTrip('the old big rule', 'the new small rule')).toEqual([
      { op: 'same', text: 'the ' },
      { op: 'del', text: 'old big' },
      { op: 'add', text: 'new small' },
      { op: 'same', text: ' rule' },
    ]);
  });

  it('handles identical and empty texts', () => {
    expect(diffWords('same text.', 'same text.')).toEqual([{ op: 'same', text: 'same text.' }]);
    expect(diffWords('', '')).toEqual([]);
    expect(diffWords('', 'new')).toEqual([{ op: 'add', text: 'new' }]);
    expect(diffWords('old', '')).toEqual([{ op: 'del', text: 'old' }]);
  });

  it('treats punctuation and whitespace changes as changes, and keeps both texts exact', () => {
    expect(roundTrip('rules; and', 'rules, and')).toEqual([
      { op: 'same', text: 'rules' },
      { op: 'del', text: ';' },
      { op: 'add', text: ',' },
      { op: 'same', text: ' and' },
    ]);
    expect(roundTrip('(a) one\n(b) two', '(a) one (b) two')).toEqual([
      { op: 'same', text: '(a) one' },
      { op: 'del', text: '\n' },
      { op: 'add', text: ' ' },
      { op: 'same', text: '(b) two' },
    ]);
    roundTrip('  leading and trailing  ', 'leading, and trailing');
    roundTrip('x', 'y');
  });

  it('is deterministic and splits sides for the columns', () => {
    const a = 'In compliance with the terms laid down in this Regulation, Member States shall lay down the rules on penalties.';
    const b = 'In accordance with the terms and conditions laid down in this Regulation, Member States shall lay down the rules on penalties and other enforcement measures.';
    const s1 = roundTrip(a, b);
    expect(diffWords(a, b)).toEqual(s1);
    expect(sideOf(s1, 'before').every((x) => x.op !== 'add')).toBe(true);
    expect(sideOf(s1, 'after').every((x) => x.op !== 'del')).toBe(true);
    expect(sideOf(s1, 'before').map((x) => x.text).join('')).toBe(a);
    expect(sideOf(s1, 'after').map((x) => x.text).join('')).toBe(b);
  });

  it('diffs two ~5k-character articles quickly, and falls back to one replacement when too large', () => {
    const words = Array.from({ length: 1100 }, (_, i) => `w${(i * 7919) % 613}`);
    const a = words.join(' ') + '.';
    const b = words.map((w, i) => (i % 9 === 0 ? 'changed' : w)).join(' ') + ' extra.';
    expect(a.length).toBeGreaterThan(4500);
    const t0 = performance.now();
    roundTrip(a, b);
    expect(performance.now() - t0).toBeLessThan(1500);
    const side = Math.ceil(Math.sqrt(MAX_CELLS)) + 10;
    const big = (p: string) => Array.from({ length: side }, (_, i) => `${p}${i}`).join('');
    const segs = roundTrip(big('a') + ' end', big('b') + ' end');
    expect(segs.map((x) => x.op)).toEqual(['del', 'add', 'same']);
  });

  it('pairs each change with its proposal and final texts', () => {
    const vms = provisionComparisons(demoSources);
    expect(vms.map((v) => v.label)).toEqual(['Art 55 → Art 62', 'Art 71 → Art 99']);
    const [sme, pen] = vms;
    expect(sme.kind).toBe('modified');
    expect(sme.before).toMatchObject({ article: 'Art 55', version: 'COM(2021) 206', stage: 'Proposal', sourceId: 'com2021_206/art_55' });
    expect(sme.after).toMatchObject({ article: 'Art 62', version: 'Regulation (EU) 2024/1689', stage: 'Final text' });
    expect(sme.before!.segments!.find((x) => x.op === 'del')?.text).toBe('small-scale providers and');
    expect(sme.after!.segments!.find((x) => x.op === 'add')?.text).toBe('SMEs, including');
    expect([sme.removed, sme.added]).toEqual([4, 2]); // "small-scale" counts as two words
    expect(sme.identical).toBe(false);
    expect(pen.identical).toBe(true);
    expect([pen.removed, pen.added]).toEqual([0, 0]);
    expect(hasComparableChanges(demoSources.changes)).toBe(true);
  });

  it('shows added and removed provisions on one side, and flags missing texts', () => {
    expect(hasComparableChanges(smeSources.changes)).toBe(false);
    expect(hasComparableChanges(undefined)).toBe(false);
    const added = provisionComparisons(smeSources)[0];
    expect(added.label).toBe('— → Art 53');
    expect(added.before).toBeNull();
    expect(added.after!.segments!.map((x) => x.op)).toEqual(['add']);
    const removed = provisionComparisons({
      scenario_id: 'x',
      changes: [
        { provision_key: 'k/removed', kind: 'removed', before: { article: '12', source_id: 'com2021_206/art_12' }, after: null },
        { provision_key: 'k/missing', kind: 'modified', before: { article: '1', source_id: 'com2021_206/art_1' }, after: { article: '2', source_id: 'reg2024_1689/art_2' } },
      ],
      sources: [{ source_id: 'com2021_206/art_12', title: 'Article 12', kind: 'provision', text: 'Gone.' }],
    });
    expect(removed[0]).toMatchObject({ label: 'Art 12 → —', after: null, removed: 1, added: 0, missingText: false });
    expect(removed[0].before!.segments).toEqual([{ op: 'del', text: 'Gone.' }]);
    expect(removed[1]).toMatchObject({ missingText: true, identical: false });
    expect(removed[1].before!.segments).toBeNull();
    expect(provisionComparisons(null)).toEqual([]);
  });
});
