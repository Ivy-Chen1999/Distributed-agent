// Overview: KPI tiles, "Needs your review", recent runs table.
import type { RunDetail, RunSummary, Scenario } from '../types';
import { ag, fmtS, kTok } from '../design';
import { RUN_ST, grounding, runStatusLabel, type RunView } from './pipeline';
import { statusAt, totalsAt } from './trace';
import { disagreementVMs, unresolvedIds } from './dossier';
import { scenarioKind, scenarioName } from './scenario';
import type { ProvisionChange } from '../types';

export const shortRunId = (id: string) => (id.startsWith('run_') ? id.slice(0, 12) : id.slice(0, 12));

export interface KpiVM {
  label: string;
  value: string;
  sub: string;
  dot?: string;
}

export function modelsUsed(run: RunDetail | null, fallback?: string | null): string {
  const ms = [...new Set((run?.usage ?? []).map((u) => u.model))];
  return ms.length ? ms.join(', ') : (fallback ?? '—');
}

export function failedExperts(rv: RunView): { agent: string; kind: string }[] {
  const out = rv.trace.experts
    .filter((e) => statusAt(rv.trace, e, rv.clock) === 'failed')
    .map((e) => ({ agent: e, kind: rv.trace.spans[e]?.errorKind ?? 'error' }));
  for (const f of rv.run?.dossier?.failed_experts ?? []) if (!out.find((o) => o.agent === f.agent)) out.push({ agent: f.agent, kind: f.error_kind });
  return out;
}

export function statusSub(rv: RunView, label: string): string {
  const run = rv.run;
  if (!run) return '';
  const failed = failedExperts(rv);
  if (label === 'Replaying' || run.status === 'running') return 'Agents are working';
  if (run.status === 'queued') return 'Waiting for a worker';
  if (run.status === 'no_changes') return 'No provision changes to assess';
  if (run.status === 'failed') return run.error ? run.error.slice(0, 80) : (run.error_kind ?? 'Run failed');
  if (failed.length) return failed.map((f) => `${ag(f.agent).n} expert ${f.kind === 'timeout' ? 'timed out' : 'failed'}`).join(', ');
  if (run.status === 'degraded') return 'Synthesis unavailable; findings listed unmerged';
  return 'All experts returned findings';
}

export function kpis(rv: RunView, runLabel: string, runDot: string, fallbackModel?: string | null): KpiVM[] {
  const { tokens, cost } = totalsAt(rv.trace, rv.run?.usage, rv.clock);
  const g = grounding(rv);
  const citeDone = statusAt(rv.trace, 'citation', rv.clock) === 'done';
  const pct = g && g.total ? Math.round((g.passed / g.total) * 100) + '%' : '—';
  return [
    { label: 'Status', value: runLabel, sub: statusSub(rv, runLabel), dot: runDot },
    { label: 'Total time', value: fmtS(rv.clock), sub: 'Wall-clock time' },
    { label: 'Tokens', value: kTok(tokens), sub: 'Input and output' },
    { label: 'Cost', value: '$' + cost.toFixed(2), sub: 'Model: ' + modelsUsed(rv.run, fallbackModel) },
    {
      label: 'Grounding',
      value: citeDone && g ? pct : '—',
      sub: citeDone && g ? `${g.passed}/${g.total} quotes verified` : 'Waiting for citation check',
    },
  ];
}

export interface AttentionVM {
  key: 'disagree' | 'questions' | 'failed' | 'unresolved';
  n: string;
  title: string;
  sub: string;
}

export function attention(rv: RunView, changes: ProvisionChange[] | null | undefined): AttentionVM[] {
  const d = rv.run?.dossier;
  const dis = d ? disagreementVMs(d, rv.run?.board, changes) : [];
  const qs = d?.open_questions ?? [];
  const unres = unresolvedIds(d);
  const failed = failedExperts(rv);
  const synthQs = qs.filter((q) => q.reason === 'synthesis').length;
  return [
    {
      key: 'disagree',
      n: String(dis.length),
      title: dis.length === 1 ? 'Disagreement' : 'Disagreements',
      sub: dis.length ? dis.map((x) => x.heading).join(' · ') : d ? 'None in this run' : 'Available when the dossier is ready',
    },
    {
      key: 'questions',
      n: String(qs.length),
      title: 'Open questions',
      sub: qs.length ? `Raised during synthesis for an analyst to settle${synthQs !== qs.length ? ` · ${qs.length - synthQs} from unresolved evidence` : ''}` : d ? 'None in this run' : 'Available when the dossier is ready',
    },
    {
      key: 'failed',
      n: String(failed.length),
      title: 'Failed experts',
      sub: failed.length ? failed.map((f) => `${ag(f.agent).n} · ${f.kind}`).join(', ') : 'None in this run',
    },
    {
      key: 'unresolved',
      n: String(unres.size),
      title: 'Unresolved evidence',
      sub: unres.size ? 'Quotes the citation check could not find in the source' : 'Every quote was found in its source',
    },
  ];
}

export interface RunRowVM {
  runId: string;
  id: string;
  name: string;
  kind: string;
  status: string;
  dot: string;
  impacts: string;
  grounding: string;
  time: string;
  sv: string;
}

export function runRows(runs: RunSummary[], scenarios: Scenario[] | null | undefined): RunRowVM[] {
  return runs.map((r) => {
    const sc = scenarios?.find((s) => s.scenario_id === r.scenario_id);
    const dur =
      r.duration_s ??
      (r.started_at && r.finished_at ? (Date.parse(r.finished_at) - Date.parse(r.started_at)) / 1000 : null);
    return {
      runId: r.run_id,
      id: shortRunId(r.run_id),
      name: scenarioName(r.scenario_id),
      kind: sc ? scenarioKind(sc) : r.scenario_id,
      status: runStatusLabel(r.status, r.error_kind),
      dot: RUN_ST[r.status]?.c ?? '#8A9699',
      impacts: r.impacts != null ? String(r.impacts) : '—',
      grounding: r.grounding && r.grounding.total ? `${r.grounding.passed}/${r.grounding.total}` : '—',
      time: dur != null && Number.isFinite(dur) ? fmtS(dur) : '—',
      sv: r.system_version,
    };
  });
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getMonth()];
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getDate()} ${mon} ${d.getFullYear()}, ${p(d.getHours())}:${p(d.getMinutes())}`;
}
