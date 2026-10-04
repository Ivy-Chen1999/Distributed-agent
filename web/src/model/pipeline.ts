// View-models for the agent graph, board feed, router panel, latency bars, log, agents, topology.
import type { DecisionRecord, ImpactDossier, ImpactFinding, RoleSpec, RunDetail, RunStatus, Scenario, SystemInfo } from '../types';
import { AG, AMBER, BLUE, GREEN, NO_DATA_IN_SCOPE, RED, ST, ag, fmtS, kTok, kindText, statusText, type NodeStatus } from '../design';
import { elapsedAt, statusAt, totalsAt, usageFor, type Trace } from './trace';
import { capitalize, unresolvedIds } from './dossier';

export interface RunView {
  trace: Trace;
  clock: number;
  run: RunDetail | null;
  system: SystemInfo | null;
  scenario?: Scenario | null;
  changesCount?: number;
}

export const ROLE: Record<string, string> = {
  diff: 'deterministic',
  planner: 'planner',
  router: 'router',
  board: 'state',
  citation: 'deterministic',
  synthesis: 'synthesis',
  dossier: 'deterministic',
};
export const roleOf = (id: string) => ROLE[id] ?? 'expert';

const SUB: Record<string, string> = {
  planner: 'Impact hypotheses',
  router: 'Relevance per expert',
  legal: 'Obligations, rights',
  fiscal: 'Costs, budgets',
  stakeholder: 'Who is affected',
  workforce: 'Jobs, skills',
  board: 'Shared findings',
  citation: 'Quote existence',
  synthesis: 'Merge and chain',
  dossier: 'Deterministic assembly',
};

const DESC: Record<string, string> = {
  planner: 'Turns the diff into an impact plan: what changed, who might be affected, which domains to check.',
  router: "Scores each expert's relevance. In shadow mode decisions are logged but every expert still runs.",
  legal: 'Reads the changed provisions for new obligations, rights, powers and liability.',
  fiscal: 'Looks for compliance cost, fees, fines and public-budget effects.',
  stakeholder: 'Maps effects onto actors and flags where burdens fall unevenly.',
  workforce: 'Candidate expert for staffing and skills effects.',
  board: 'One shared board every expert posts to. Findings keep their provenance: agent, prompt hash, model, round.',
  citation: 'Checks every evidence quote verbatim against the source text. Grounding = quote existence rate.',
  synthesis: 'Merges overlapping findings into impacts, links causal chains, surfaces disagreements and open questions.',
  dossier: 'Assembles the final Impact Dossier. Every impact traces to a provision key, a quote and a source id.',
};

export function nodeSub(id: string, rv: RunView): string {
  if (id === 'diff') return rv.changesCount != null ? `${rv.changesCount} changed provisions` : 'Changed provisions';
  return SUB[id] ?? 'Domain expert';
}

export function nodeDesc(id: string, rv: RunView): string {
  if (id === 'diff') {
    const sc = rv.scenario;
    return sc && !sc.before_version
      ? 'Compares provision keys between versions. No prior version here, so every provision in scope counts as added.'
      : 'Compares provision keys between versions, following renumbered articles across texts.';
  }
  const base = DESC[id] ?? `Domain expert for ${id} effects.`;
  const sp = rv.trace.spans[id];
  if (sp?.failed && roleOf(id) === 'expert') return `${base} ${sp.errorKind === 'timeout' ? 'Timed out' : 'Failed'} in this run.`;
  return base;
}

export function roleSpec(system: SystemInfo | null, id: string): RoleSpec | undefined {
  if (!system) return undefined;
  return roleOf(id) === 'expert' ? system.roles[`expert:${id}`] : system.roles[id];
}

// ---------- run status ----------

export const RUN_ST: Record<RunStatus, { t: string; c: string }> = {
  queued: { t: 'Queued', c: ST.queued.c },
  running: { t: 'Running', c: BLUE },
  succeeded: { t: 'Succeeded', c: GREEN },
  degraded: { t: 'Degraded', c: AMBER },
  failed: { t: 'Failed', c: RED },
  no_changes: { t: 'No changes', c: ST.queued.c },
};

export function runStatusLabel(status: RunStatus, errorKind?: string | null): string {
  return status === 'failed' && errorKind ? `Failed · ${kindText(errorKind)}` : RUN_ST[status]?.t ?? status;
}

// ---------- graph ----------

export function edges(experts: string[]): [string, string][] {
  return [
    ['diff', 'planner'],
    ['planner', 'router'],
    ...experts.map((e) => ['router', e] as [string, string]),
    ...experts.map((e) => [e, 'board'] as [string, string]),
    ['board', 'citation'],
    ['citation', 'synthesis'],
    ['board', 'synthesis'],
    ['synthesis', 'dossier'],
  ];
}

export function colIds(experts: string[]): string[][] {
  return [['diff'], ['planner'], ['router'], experts, ['board'], ['citation'], ['synthesis'], ['dossier']];
}

export interface NodeVM {
  id: string;
  name: string;
  c: string;
  sub: string;
  status: NodeStatus;
  stT: string;
  stC: string;
  meta: string;
  shadow: boolean;
}

export function grounding(rv: RunView): { passed: number; total: number } | null {
  const g = rv.run?.grounding ?? rv.trace.spans.citation?.payload?.grounding;
  return g ?? null;
}

export function nodeVM(id: string, rv: RunView): NodeVM {
  const { trace, clock } = rv;
  const s = statusAt(trace, id, clock);
  const el = s === 'queued' || s === 'skipped' ? '—' : fmtS(elapsedAt(trace, id, clock));
  const u = usageFor(rv.run?.usage, id);
  const g = grounding(rv);
  let meta = el;
  if (u && s === 'done') meta += ' · ' + kTok(u.tin + u.tout) + ' tok';
  else if (id === 'citation' && s === 'done' && g) meta += ` · ${g.passed}/${g.total}`;
  const mode = rv.run?.decisions?.[0]?.mode ?? trace.spans.router?.payload?.decisions?.[0]?.mode ?? rv.system?.router.mode;
  return {
    id,
    name: ag(id).n,
    c: ag(id).c,
    sub: nodeSub(id, rv),
    status: s,
    stT: statusText(s, trace.spans[id]?.errorKind),
    stC: ST[s].c,
    meta,
    shadow: id === 'router' && mode !== 'active',
  };
}

// ---------- strip / KPIs / latency ----------

export function stripVM(rv: RunView) {
  const { tokens, cost } = totalsAt(rv.trace, rv.run?.usage, rv.clock);
  const g = grounding(rv);
  const citeDone = statusAt(rv.trace, 'citation', rv.clock) === 'done';
  return [
    { value: fmtS(Math.min(rv.clock, rv.trace.finished ? rv.trace.end : rv.clock)), label: 'elapsed' },
    { value: kTok(tokens), label: 'tokens' },
    { value: '$' + cost.toFixed(2), label: 'cost' },
    { value: citeDone && g ? `${g.passed}/${g.total}` : '—', label: 'quotes verified' },
  ];
}

export interface LatBarVM {
  id: string;
  name: string;
  c: string;
  w: string;
  bar: string;
  tc: string;
  meta: string;
}

export function latBars(rv: RunView): LatBarVM[] {
  const ids = ['planner', ...rv.trace.experts, 'synthesis'];
  const els = ids.map((id) => elapsedAt(rv.trace, id, rv.clock));
  const finals = ids.map((id) => elapsedAt(rv.trace, id, Infinity));
  const max = Math.max(1, ...finals, ...els);
  return ids.map((id, i) => {
    const s = statusAt(rv.trace, id, rv.clock);
    const u = usageFor(rv.run?.usage, id);
    const kind = rv.trace.spans[id]?.errorKind;
    const meta =
      s === 'failed'
        ? `${kind ?? 'failed'} · ${fmtS(els[i])}`
        : s === 'queued'
          ? 'queued'
          : s === 'skipped'
            ? 'skipped'
            : fmtS(els[i]) + (s === 'done' && u ? ' · $' + u.cost.toFixed(2) : '');
    return {
      id,
      name: ag(id).n,
      c: ag(id).c,
      w: ((els[i] / max) * 100).toFixed(1) + '%',
      bar: s === 'failed' ? RED : ag(id).c,
      tc: s === 'failed' ? RED : 'var(--n1)',
      meta,
    };
  });
}

// ---------- router decisions ----------

export interface DecisionVM {
  id: string;
  name: string;
  c: string;
  w: string;
  bar: string;
  decision: string;
  tc: string;
}

export function decisionRecords(rv: RunView): DecisionRecord[] {
  return rv.run?.decisions ?? rv.trace.spans.router?.payload?.decisions ?? [];
}

export function decisionVMs(rv: RunView): DecisionVM[] {
  const routerDone = statusAt(rv.trace, 'router', rv.clock) === 'done';
  const recs = decisionRecords(rv);
  return rv.trace.experts.map((e) => {
    const d = recs.find((r) => r.subject === e);
    const err = d?.decision === 'error';
    const w = !routerDone ? '0%' : d?.probability != null ? `${Math.round(d.probability * 100)}%` : '100%';
    return {
      id: e,
      name: ag(e).n,
      c: ag(e).c,
      w,
      bar: err ? RED : '#00C4CC',
      decision: !routerDone ? 'pending' : d ? d.decision.replace(/_/g, ' ') : 'dispatched',
      tc: err ? RED : 'var(--ink)',
    };
  });
}

export function routerMode(rv: RunView): string {
  return decisionRecords(rv)[0]?.mode ?? rv.system?.router.mode ?? 'shadow';
}

export function routerDecider(rv: RunView): string {
  return decisionRecords(rv)[0]?.decider ?? rv.system?.router.decider ?? 'stub';
}

export function routerNote(mode: string): string {
  if (mode === 'active') return 'Enforce mode skips experts marked not relevant. Switch once Jev is calibrated.';
  if (mode === 'off') return 'Router is off: every expert runs and no decisions are logged.';
  return 'Shadow mode logs each decision but runs every expert anyway.';
}

// ---------- board feed ----------

export interface FeedVM {
  key: string;
  c: string;
  agentName: string;
  provision: string;
  impact: string;
  actor: string;
  conf: string;
  badge: 'Checking quote' | 'Quote verified' | 'Quote not found' | 'Quotes checked';
  findingId?: string;
}

export interface Feed {
  items: FeedVM[];
  total: number;
}

export function feedVM(rv: RunView, board: ImpactFinding[] | null | undefined, dossier: ImpactDossier | null | undefined): Feed {
  const { trace, clock } = rv;
  const cite = statusAt(trace, 'citation', clock);
  const citeDone = cite === 'done';
  const unres = unresolvedIds(dossier);
  if (board && board.length) {
    const per: Record<string, number> = {};
    const rows = board.map((f) => {
      const k = (per[f.agent] = (per[f.agent] ?? -1) + 1);
      const at = (trace.spans[f.agent]?.e ?? trace.end) + k * 0.05;
      return { f, at, k };
    });
    const vis = rows.filter((r) => r.at <= clock).sort((a, b) => b.at - a.at);
    return {
      total: board.length,
      items: vis.map(({ f }) => ({
        key: f.finding_id,
        c: ag(f.agent).c,
        agentName: ag(f.agent).n,
        provision: f.provision_key,
        impact: f.impact,
        actor: capitalize(f.affected_actor),
        conf: f.confidence.toFixed(2),
        badge: !citeDone ? 'Checking quote' : unres.has(f.finding_id) ? 'Quote not found' : 'Quote verified',
        findingId: f.finding_id,
      })),
    };
  }
  // Live run: only counts are known until the run finishes.
  let total = 0;
  const items: (FeedVM & { at: number })[] = [];
  for (const e of trace.experts) {
    const sp = trace.spans[e];
    const n = sp?.payload?.findings?.[e] ?? 0;
    total += n;
    if (sp?.e === undefined || sp.e > clock || sp.failed) continue;
    items.push({
      at: sp.e,
      key: `count_${e}`,
      c: ag(e).c,
      agentName: ag(e).n,
      provision: `expert_${e}`,
      impact: `Posted ${n} finding${n === 1 ? '' : 's'} to the shared board.`,
      actor: 'Finding details open when the run finishes',
      conf: '—',
      badge: citeDone ? 'Quotes checked' : 'Checking quote',
    });
  }
  items.sort((a, b) => b.at - a.at);
  return { total, items };
}

// ---------- event log ----------

export interface LogLine {
  key: string;
  ts: string;
  level: 'INFO' | 'WARN' | 'ERROR';
  node: string;
  c: string;
  msg: string;
}

const LOG_NODE: Record<string, string> = { citation: 'citation', dossier: 'dossier' };

export function clockTime(t0: number | null, rel: number): string {
  if (t0 === null) return fmtS(rel);
  const d = new Date(t0 + rel * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getHours()}:${p(d.getMinutes())}:${p(d.getSeconds())}.${Math.floor(d.getMilliseconds() / 100)}`;
}

export function logLines(rv: RunView): LogLine[] {
  const { trace, clock } = rv;
  const out: LogLine[] = [];
  const add = (key: string, rel: number, level: LogLine['level'], node: string, msg: string) => {
    const known = AG[node] ?? (trace.experts.includes(node) ? ag(node) : undefined);
    if (rel <= clock) out.push({ key, ts: clockTime(trace.t0, rel), level, node, c: known?.c ?? '#D5DEDF', msg });
  };
  if (trace.events.length) {
    add('run', 0, 'INFO', 'run', `started · scenario ${rv.run?.scenario_id ?? '—'} · ${rv.run?.system_version ?? ''}`);
  }
  const diffEnd = trace.spans.diff?.e;
  if (diffEnd !== undefined) add('diff', diffEnd, 'INFO', 'diff', `${rv.changesCount ?? '?'} changed provisions`);
  let dispatchedLogged = false;
  for (const ev of trace.events) {
    const id = ev.id ?? ev.node;
    const node = LOG_NODE[id] ?? id;
    const p = ev.payload ?? {};
    const lat = trace.spans[id]?.s !== undefined ? ev.rel - (trace.spans[id].s as number) : 0;
    if (ev.event === 'started') {
      const isExpert = ev.id !== null && roleOf(ev.id) === 'expert';
      if (isExpert) {
        if (!dispatchedLogged) {
          dispatchedLogged = true;
          add(`dispatch`, ev.rel, 'INFO', 'dispatch', `experts started in parallel ×${trace.experts.length}`);
        }
        continue;
      }
      continue;
    }
    if (ev.event === 'failed') {
      add(`e${ev.seq}`, ev.rel, 'ERROR', node, `failed · ${p.error ?? 'error'}`);
      continue;
    }
    const failures = Object.entries(p.failures ?? {});
    let msg: string;
    let level: LogLine['level'] = 'INFO';
    if (id === 'planner') {
      const u = usageFor(rv.run?.usage, 'planner');
      msg = p.error
        ? `failed · ${p.error}`
        : `impact plan ready${p.focus_areas != null ? ` · ${p.focus_areas} focus areas` : ''}${u ? ` · ${u.tin.toLocaleString('en')} in / ${u.tout.toLocaleString('en')} out` : ''} · ${fmtS(lat)}`;
      if (p.error) level = 'ERROR';
    } else if (id === 'router') {
      const ds = p.decisions ?? [];
      msg = ds.length
        ? ds.map((d) => `${d.subject}=${d.decision}`).join(' ') + ` · mode=${ds[0].mode} decider=${ds[0].decider}`
        : `dispatched ${(p.dispatched ?? []).join(', ') || 'all experts'} · router off`;
    } else if (ev.id !== null && roleOf(ev.id) === 'expert') {
      const errors = failures.filter(([, k]) => k !== NO_DATA_IN_SCOPE);
      if (errors.length) {
        level = 'WARN';
        msg = errors.map(([, k]) => `error_kind=${k} after ${fmtS(lat)}`).join(' · ') + ' · run continues as degraded';
      } else if (failures.length) {
        // A scoped expert with nothing in its scope is not called: data, not an error.
        msg = 'skipped · no data within its scope (no call)';
      } else {
        const n = p.findings?.[id];
        msg = `posted ${n ?? 0} findings to board · ${fmtS(lat)}`;
      }
    } else if (id === 'citation') {
      const g = p.grounding;
      const un = p.unsupported ?? 0;
      level = un ? 'WARN' : 'INFO';
      msg = g ? `${g.passed}/${g.total} quotes found verbatim${un ? ` · ${un} unresolved → open question${un === 1 ? '' : 's'}` : ''}` : 'citation check done';
    } else if (id === 'synthesis') {
      if (p.error) {
        level = 'WARN';
        msg = `failed · ${p.error} · findings listed unmerged`;
      } else {
        const d = rv.run?.dossier;
        msg = d
          ? `${d.impacts.length} impacts · ${d.chains.length} chains · ${d.disagreements.length} disagreement${d.disagreements.length === 1 ? '' : 's'} · ${d.open_questions.length} open questions · ${fmtS(lat)}`
          : `merged board · ${fmtS(lat)}`;
      }
    } else if (id === 'dossier') {
      level = p.status === 'failed' ? 'ERROR' : p.status === 'degraded' ? 'WARN' : 'INFO';
      msg = `assembled · status=${p.status ?? '?'}${p.impacts != null ? ` · ${p.impacts} impacts` : ''}`;
    } else {
      msg = `${ev.event}`;
    }
    add(`e${ev.seq}`, ev.rel, level, node, msg);
    // Log the board once the last expert has posted.
    if (ev.id !== null && roleOf(ev.id) === 'expert' && trace.spans.board?.e === ev.rel && trace.spans.board.e !== undefined) {
      const total = rv.run?.board?.reduce((a, f) => a + f.evidence.length, 0);
      add('board', ev.rel, 'INFO', 'board', total != null ? `${total} evidence quotes collected` : 'all experts posted');
    }
  }
  return out;
}

// ---------- selected node / inspector ----------

export interface SelInfo {
  id: string;
  name: string;
  c: string;
  desc: string;
  stT: string;
  stC: string;
  facts: { k: string; v: string }[];
  ins: string[];
  outs: string[];
}

export function selInfo(id: string, rv: RunView): SelInfo {
  const { trace, clock } = rv;
  const s = statusAt(trace, id, clock);
  const role = roleOf(id);
  const facts: { k: string; v: string }[] = [
    { k: 'Role', v: role },
    { k: 'Latency', v: s === 'queued' || s === 'skipped' ? '—' : fmtS(elapsedAt(trace, id, clock)) },
  ];
  const u = usageFor(rv.run?.usage, id);
  if (u) facts.push({ k: 'Tokens in / out', v: kTok(u.tin) + ' / ' + kTok(u.tout) }, { k: 'Cost', v: '$' + u.cost.toFixed(3) });
  const spec = roleSpec(rv.system, id);
  const prov = rv.run?.board?.find((f) => f.agent === id)?.provenance;
  if (spec || u || prov) {
    facts.push({ k: 'Backend', v: u?.backend ?? prov?.backend ?? spec?.backend ?? '—' }, { k: 'Model', v: u?.model ?? prov?.model ?? spec?.model ?? '—' });
    if (spec?.prompt) facts.push({ k: 'Prompt', v: spec.prompt });
    const hash = prov?.prompt_hash ?? spec?.prompt_hash;
    if (hash) facts.push({ k: 'Prompt hash', v: hash });
  }
  if (id === 'router') {
    facts.push({ k: 'Mode', v: routerMode(rv) }, { k: 'Decider', v: routerDecider(rv) });
  }
  if (id === 'citation') {
    const g = grounding(rv);
    if (g && s === 'done') facts.push({ k: 'Quotes verified', v: `${g.passed}/${g.total}` });
  }
  const kind = trace.spans[id]?.errorKind;
  if (s === 'failed' && kind) facts.push({ k: 'error_kind', v: kind });
  const E = edges(trace.experts);
  return {
    id,
    name: ag(id).n,
    c: ag(id).c,
    desc: nodeDesc(id, rv),
    stT: statusText(s, kind),
    stC: ST[s].c,
    facts,
    ins: E.filter((e) => e[1] === id).map((e) => e[0]),
    outs: E.filter((e) => e[0] === id).map((e) => e[1]),
  };
}

// ---------- agents ----------

export interface AgentCardVM {
  id: string;
  name: string;
  c: string;
  role: string;
  desc: string;
  status: NodeStatus;
  stT: string;
  stC: string;
  stats: { k: string; v: string }[];
  tags: string[];
}

export function agentCards(rv: RunView): AgentCardVM[] {
  const ids = ['planner', 'router', ...rv.trace.experts, 'synthesis'];
  return ids.map((id) => {
    const s = statusAt(rv.trace, id, rv.clock);
    const u = usageFor(rv.run?.usage, id);
    const spec = roleSpec(rv.system, id);
    const kind = rv.trace.spans[id]?.errorKind;
    const lat = elapsedAt(rv.trace, id, rv.clock);
    const stats =
      id === 'router'
        ? [
            { k: 'decisions', v: String(decisionRecords(rv).length || rv.trace.experts.length) },
            { k: 'mode', v: routerMode(rv) },
            { k: 'decider', v: routerDecider(rv) },
          ]
        : u
          ? [
              { k: 'latency', v: fmtS(u.lat || lat) },
              { k: 'tokens', v: kTok(u.tin + u.tout) },
              { k: 'cost', v: '$' + u.cost.toFixed(2) },
            ]
          : [
              { k: 'latency', v: s === 'queued' || s === 'skipped' ? '—' : fmtS(lat) },
              { k: 'tokens', v: '—' },
              { k: 'error', v: s === 'failed' ? (kind ?? 'error') : '—' },
            ];
    const prov = rv.run?.board?.find((f) => f.agent === id)?.provenance;
    const tags =
      id === 'router'
        ? ['router.relevance', routerDecider(rv) === 'jev' ? 'Jev' : 'stub decider']
        : [spec?.prompt, prov?.prompt_hash ?? spec?.prompt_hash, u?.model ?? spec?.model].filter((x): x is string => !!x);
    return {
      id,
      name: ag(id).n,
      c: ag(id).c,
      role: roleOf(id),
      desc: nodeDesc(id, rv),
      status: s,
      stT: statusText(s, kind),
      stC: ST[s].c,
      stats,
      tags,
    };
  });
}

// ---------- topology ----------

export function topoPositions(experts: string[]): Record<string, [number, number]> {
  const n = Math.max(1, experts.length);
  const top = n <= 3 ? 100 : 75;
  const bottom = n <= 3 ? 380 : 405;
  const step = n > 1 ? (bottom - top) / (n - 1) : 0;
  const pos: Record<string, [number, number]> = {
    diff: [6, 240],
    planner: [19, 240],
    router: [33, 240],
    board: [67, 240],
    citation: [80, 130],
    synthesis: [80, 350],
    dossier: [94, 240],
  };
  experts.forEach((e, i) => (pos[e] = [50, n === 1 ? 240 : top + step * i]));
  return pos;
}

export function hostLabel(id: string, system: SystemInfo | null, rv?: RunView): string {
  const role = roleOf(id);
  if (role === 'expert' || role === 'planner' || role === 'synthesis') {
    const b = (rv && usageFor(rv.run?.usage, id)?.backend) ?? roleSpec(system, id)?.backend;
    if (b === 'claude_code') return 'claude -p · isolated';
    if (b) return b;
  }
  return role;
}

export function isIsolated(id: string, system: SystemInfo | null, rv?: RunView): boolean {
  return hostLabel(id, system, rv) === 'claude -p · isolated';
}
