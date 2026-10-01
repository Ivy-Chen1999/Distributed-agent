// Test data: the real sample run plus a synthetic event stream with the design's timings.
import sample from '../../../docs/ui/sample_run.json';
import sources from './sources_sme.json';
import type { RunDetail, RunEvent, Scenario, ScenarioSources, SystemInfo } from '../types';

export const T0 = Date.parse('2026-09-28T16:18:27.200Z');
const at = (s: number) => new Date(T0 + s * 1000).toISOString();

export const sampleRun = (): RunDetail =>
  structuredClone({
    ...(sample as unknown as RunDetail),
    created_at: at(0),
    started_at: at(0),
    finished_at: at(287.5),
  });

export const smeSources = sources as unknown as ScenarioSources;

type Ev = [number, string, string, RunEvent['payload']?];

const decisions = ['legal', 'fiscal', 'stakeholder'].map((subject) => ({ subject, decision: 'relevant', probability: null, mode: 'shadow' as const, decider: 'stub' }));

export function syntheticEvents(opts: { workforce?: boolean } = {}): RunEvent[] {
  const ex = opts.workforce ? ['legal', 'fiscal', 'stakeholder', 'workforce'] : ['legal', 'fiscal', 'stakeholder'];
  const rows: Ev[] = [
    [0.4, 'planner', 'started'],
    [26.8, 'planner', 'finished', { focus_areas: 4 }],
    [26.8, 'router', 'started'],
    [27.0, 'router', 'finished', { decisions, dispatched: ex }],
    ...ex.map((e): Ev => [27.0, `expert_${e}`, 'started']),
    [71.4, 'expert_fiscal', 'finished', { findings: { fiscal: 10 } }],
    ...(opts.workforce ? [[87.0, 'expert_workforce', 'finished', { failures: { workforce: 'timeout' } }] as Ev] : []),
    [125.7, 'expert_stakeholder', 'finished', { findings: { stakeholder: 12 } }],
    [130.0, 'expert_legal', 'finished', { findings: { legal: 22 } }],
    [130.2, 'validate', 'started'],
    [132.0, 'validate', 'finished', { grounding: { passed: 64, total: 64 }, supported: 44, unsupported: 0 }],
    [132.0, 'synthesis', 'started'],
    [287.2, 'synthesis', 'finished', {}],
    [287.2, 'assemble', 'started'],
    [287.5, 'assemble', 'finished', { status: opts.workforce ? 'degraded' : 'succeeded', impacts: 25 }],
  ];
  return rows.map(([s, node, event, payload], i) => ({ seq: i + 1, node, event, payload: payload ?? {}, at: at(s) }));
}

/** A real stakeholder finding on penalties, used as the unresolved one in the degraded variant. */
export const UNRESOLVED_ID = (sample as unknown as RunDetail).board!.find((f) => f.agent === 'stakeholder' && f.provision_key === 'ai_act/penalties/penalties')!.finding_id;

/** A degraded variant of the sample run: workforce timed out, one finding's quote unresolved. */
export function degradedRun(): RunDetail {
  const r = sampleRun();
  r.status = 'degraded';
  r.failures = [{ agent: 'workforce', error_kind: 'timeout', message: 'claude CLI timed out after 60s', attempts: 1 }];
  const d = r.dossier!;
  d.status = 'degraded';
  d.failed_experts = r.failures;
  d.open_questions.push({ question: 'Unverified (stakeholder): penalty leniency', finding_id: UNRESOLVED_ID, reason: 'evidence_unresolved' });
  r.grounding = { passed: 63, total: 64 };
  return r;
}

export const scenarios: Scenario[] = [
  {
    scenario_id: 'eval_provider_compliance_costs',
    kind: 'evaluation',
    description: 'Proposal as a whole-new text: requirements for high-risk AI systems. Who bears which compliance costs and administrative burdens?',
    before_version: null,
    after_version: 'com2021_206',
    provision_keys: ['ai_act/high_risk/risk_management'],
    ia_reference: 'SWD(2021) 84 Part 1, section 6.1.3 (costs and administrative burdens)',
  },
  {
    scenario_id: 'eval_sme_impacts',
    kind: 'evaluation',
    description:
      'Proposal as a whole-new text: AI regulatory sandboxes (Art 53-54), measures for small-scale providers and users (Art 55) and penalties (Art 71). How are SMEs and start-ups affected?',
    before_version: null,
    after_version: 'com2021_206',
    provision_keys: ['ai_act/innovation/regulatory_sandboxes', 'ai_act/innovation/sandbox_personal_data', 'ai_act/innovation/sme_measures', 'ai_act/penalties/penalties'],
    ia_reference: 'SWD(2021) 84 Part 1, section 6.1.4 (SME test)',
  },
];

const role = (prompt: string, hash: string) => ({ backend: 'claude_code', model: 'claude-sonnet-5', prompt, prompt_hash: hash });

export const system: SystemInfo = {
  version_id: 'sv_44a681965332',
  name: 'v0-baseline',
  source_path: 'system_versions/v0-baseline.yaml',
  description: 'Friday smoke baseline.',
  roles: {
    planner: role('prompts/planner.md', '3f1c9a02be7d4410'),
    synthesis: role('prompts/synthesis.md', 'c4e19f7a0b2d6358'),
    judge: role('prompts/judge_coverage.md', '9a9a9a9a9a9a9a9a'),
    'expert:legal': role('prompts/legal.md', '7cabd9f203ee1062'),
    'expert:fiscal': role('prompts/fiscal.md', '0e111403b501a9ac'),
    'expert:stakeholder': role('prompts/stakeholder.md', 'a28ccbceb3292ba1'),
  },
  experts: ['legal', 'fiscal', 'stakeholder'].map((id) => ({ id, domain: id, backend: 'claude_code', model: 'claude-sonnet-5' })),
  router: { mode: 'shadow', decider: 'stub' },
  max_parallel_llm_calls: 3,
  available_backends: ['claude_code', 'api'],
  backend_ready: true,
  backend_error: null,
  code: { git_sha: 'ca8a81d28487', dirty: false, claude_cli_version: '2.1.283 (Claude Code)' },
  self_check: { passed: true, checks: { auth_preflight: true, no_tools: true, no_mcp_servers: true, no_memory: true, canary_absent: true }, problems: [] },
};

export interface MockCall {
  method: string;
  url: string;
  body: unknown;
  auth: string | null;
}

/** Install a fetch mock that answers the WOMM API from the fixtures. */
export function mockApi(overrides: Record<string, (call: MockCall) => { status: number; body: unknown } | undefined> = {}) {
  const calls: MockCall[] = [];
  const run = sampleRun();
  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  const fn = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname + input.search : input.url;
    const headers = new Headers(init?.headers);
    const call: MockCall = { method: init?.method ?? 'GET', url, body: init?.body ? JSON.parse(String(init.body)) : null, auth: headers.get('Authorization') };
    calls.push(call);
    for (const [prefix, h] of Object.entries(overrides)) {
      if (url.startsWith(prefix)) {
        const r = h(call);
        if (r) return json(r.status, r.body);
      }
    }
    const path = url.split('?')[0];
    if (path === '/system') return json(200, system);
    if (path === '/scenarios') return json(200, scenarios);
    if (path === '/scenarios/eval_sme_impacts/sources') return json(200, smeSources);
    if (path === '/runs' && call.method === 'GET')
      return json(200, {
        runs: [
          { run_id: run.run_id, scenario_id: run.scenario_id, status: run.status, system_version: run.system_version, created_at: run.created_at, started_at: run.started_at, finished_at: run.finished_at, duration_s: 287.5, impacts: 25, grounding: run.grounding, error_kind: null },
        ],
      });
    if (path === '/runs' && call.method === 'POST') return json(202, { run_id: 'run_new00000-0000', status: 'queued', system_version: 'sv_derived0000' });
    if (path === `/runs/${run.run_id}`) return json(200, run);
    if (path === `/runs/${run.run_id}/events`) {
      const after = Number(new URL(url, 'http://x').searchParams.get('after') ?? 0);
      const evs = syntheticEvents().filter((e) => e.seq > after);
      return json(200, { run_id: run.run_id, events: evs, next_after: evs.length ? evs[evs.length - 1].seq : after });
    }
    if (path === `/runs/${run.run_id}/ask`) return json(200, { answer: 'Penalty risk sits with all providers (I19).', cites: ['I19', 'f_083bb47fcfc7'], covered: true });
    return json(404, { detail: `no mock for ${path}` });
  };
  return { fn, calls, run };
}
