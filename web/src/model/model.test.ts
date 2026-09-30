import { describe, expect, it } from 'vitest';
import { buildTrace, elapsedAt, liveClock, statusAt, totalsAt, usageFor } from './trace';
import { agentCards, decisionVMs, feedVM, latBars, logLines, nodeVM, runStatusLabel, selInfo, stripVM, type RunView } from './pipeline';
import { attention, kpis, runRows } from './overview';
import { actorGroup, chainVMs, disagreementVMs, findingSteps, groupImpacts, questionVMs, shortLabel, unresolvedIds } from './dossier';
import { highlightQuote, normalizeWithMap } from './quote';
import { articleRanges, regulationSub, regulationTitle, scenarioBlurb, scenarioHeadline, scenarioKind, scenarioName } from './scenario';
import { UNRESOLVED_ID, degradedRun, sampleRun, scenarios, smeSources, syntheticEvents, system } from '../test/fixtures';
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
