// Whole-act coverage and LangSmith trace links on Run detail / Analysis, and the published
// evaluation on Overview.
import { afterEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { App } from '../App';
import { TOKEN_KEY } from '../api';
import { ROLE_KEY } from '../model/roles';
import { mockApi, sampleRun, system, type MockCall } from '../test/fixtures';
import type { EvalReport, RunDetail } from '../types';

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  window.location.hash = '';
});

const TRACE_URL = 'https://smith.langchain.com/o/t/projects/p/p1/r/abc?trace_id=abc&poll=true';

function boot(run: RunDetail, role: 'law' | 'system', extra: Record<string, (c: MockCall) => { status: number; body: unknown } | undefined> = {}) {
  const mock = mockApi({
    '/runs?': () => ({ status: 200, body: { runs: [{ run_id: run.run_id, scenario_id: run.scenario_id, status: run.status, system_version: run.system_version }] } }),
    [`/runs/${run.run_id}`]: (c) => (c.url.split('?')[0] === `/runs/${run.run_id}` ? { status: 200, body: run } : undefined),
    ...extra,
  });
  vi.stubGlobal('fetch', vi.fn(mock.fn));
  localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
  localStorage.setItem(ROLE_KEY, role);
  window.location.hash = `#/detail?run=${run.run_id}`;
  render(<App />);
}

const wholeAct = (): RunDetail => ({
  ...sampleRun(),
  planner: {
    keys: ['ai_act/art/5'],
    areas: [['ai_act/art/5']],
    notes: [],
    dispatched: ['legal', 'fiscal', 'stakeholder'],
    selected: ['ai_act/art/5', 'ai_act/art/26'],
    dropped: [{ key: 'ai_act/art/26', reason: 'max_prompt_chars' }],
    memorandum: { legal: ['com2021_206/memorandum/legal_basis', 'com2021_206/memorandum/other_elements/5.2.2'] },
  },
  trace: { url: TRACE_URL },
});

it('the law Analysis page says which provisions were not analysed, and shows no trace link', async () => {
  boot(wholeAct(), 'law');
  const note = await screen.findByTestId('analysis-coverage');
  expect(note.textContent).toBe('This analysis covers 1 of the 2 provisions it selected. Not analysed: Art 26 (too long to fit in one analysis).');
  expect(screen.queryByText('Open trace in LangSmith')).toBeNull();
});

it('system Run detail links the trace in a new tab and shows coverage and memorandum parts', async () => {
  boot(wholeAct(), 'system');
  const link = await screen.findByText('Open trace in LangSmith');
  expect(link.getAttribute('href')).toBe(TRACE_URL);
  expect(link.getAttribute('target')).toBe('_blank');
  expect(link.getAttribute('rel')).toContain('noopener');
  const line = screen.getByTestId('run-coverage');
  expect(line.textContent).toContain('1 of 2 selected provisions analysed');
  expect(line.textContent).toContain('Legal basis, 5.2.2');
});

it('an untraced run says why, and a preset run shows no coverage', async () => {
  boot({ ...sampleRun(), trace: { reason: 'Not traced: tracing was off on this server when the run started.' } }, 'system');
  expect((await screen.findByTestId('trace-reason')).textContent).toMatch(/tracing was off/);
  expect(screen.queryByTestId('run-coverage')).toBeNull();
});

const evalReport: EvalReport = {
  report_id: 3,
  system_version: system.version_id,
  created_at: '2026-11-13T18:00:00Z',
  repetitions: 2,
  experiment_url: 'https://smith.langchain.com/o/t/projects/p/exp',
  summary: {
    cases: 11, splits: { train: 8, val: 3 }, runs: 22, excluded_runs: 0,
    reference_impacts: { value: 0.79, range: [0.65, 0.88], found: 41, total: 52 },
    omissions_addressed: { value: 0.8, range: [0.7, 0.9], found: 8, total: 10 },
    grounding: { value: 0.97, range: [0.95, 0.99] },
    precision: { value: null, range: null },
  },
};

it('Overview shows the published evaluation of the running version', async () => {
  boot(sampleRun(), 'system', { '/eval-reports/latest': () => ({ status: 200, body: evalReport }) });
  window.location.hash = '#/overview';
  const card = await screen.findByRole('region', { name: 'Evaluation' });
  expect(await within(card).findByText('41 of 52')).toBeTruthy();
  expect(within(card).getByText('about 65–88%')).toBeTruthy();
  expect(within(card).getByText(/8 train and 3 val reference cases, 2 runs each/)).toBeTruthy();
  expect(within(card).getByText('Open the experiment in LangSmith').getAttribute('href')).toBe(evalReport.experiment_url);
});

it('Overview says when nothing is published for the running version', async () => {
  boot(sampleRun(), 'system');
  window.location.hash = '#/overview';
  const card = await screen.findByRole('region', { name: 'Evaluation' });
  expect(await within(card).findByText('No evaluation published for this version.')).toBeTruthy();
});
