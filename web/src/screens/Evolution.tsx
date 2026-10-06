// Evolution (R36 page 4): the self-evolution lineage, each candidate's config diff, train/val
// aggregates, the holdout decision (published summaries only), the new-expert highlight and
// the R37 diff check. Desktop layout; every label and reason comes from the API.
import { useState, type ReactNode } from 'react';
import { api, getToken } from '../api';
import { CARD, FONT, LABEL, RED, TABULAR, WARN_BG, WARN_INK } from '../design';
import { useLoad } from '../hooks';
import { Dot, EmptyCard, ErrorCard, LoadingCard, OUTLINE_BTN } from '../components/ui';
import {
  MODE_TEXT,
  badge,
  ciText,
  defaultSelection,
  fmtDelta,
  lineDiffKind,
  metricName,
  patternText,
  r37Line,
  shortId,
  summaryLines,
  treeRows,
  withNoise,
} from '../model/evolution';
import type { CandidateDetail, LineageNode, PromotionDecision } from '../types';

const MONO = "ui-monospace,'SFMono-Regular',Menlo,monospace";

export function Badge({ b }: { b: string }) {
  const s = badge(b);
  return (
    <span data-badge={b} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, border: '1px solid var(--line)', borderRadius: 4, padding: '1px 7px', font: `700 11px ${FONT}`, color: 'var(--ink)', whiteSpace: 'nowrap' }}>
      <Dot c={s.c} size={6} />
      {s.t}
    </span>
  );
}

function Section({ title, children, testId }: { title: string; children: ReactNode; testId?: string }) {
  return (
    <section data-testid={testId} aria-label={title} style={{ ...CARD, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <h3 style={{ ...LABEL, margin: 0 }}>{title}</h3>
      {children}
    </section>
  );
}

function LineageTree({ nodes, selected, onSelect }: { nodes: LineageNode[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <ul aria-label="Lineage" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
      {treeRows(nodes).map(({ node, depth }) => {
        const on = node.version_id === selected;
        return (
          <li key={node.version_id} style={{ paddingLeft: depth * 18 }}>
            <button
              type="button"
              data-version={node.version_id}
              aria-pressed={on}
              onClick={() => onSelect(node.version_id)}
              style={{ width: '100%', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 5, padding: '9px 11px', borderRadius: 8, cursor: 'pointer', background: on ? 'var(--accSoft)' : 'transparent', border: `1px solid ${on ? 'var(--accInk)' : 'var(--line)'}`, color: 'var(--ink)', font: `500 13px ${FONT}` }}
            >
              <span style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
                {depth > 0 && <span aria-hidden style={{ color: 'var(--n2)' }}>└</span>}
                <span style={{ fontWeight: 700, ...TABULAR }}>{shortId(node.version_id)}</span>
                {node.twins.length > 0 && <span style={{ fontSize: 11, color: 'var(--n2)' }}>+{node.twins.length} api twin{node.twins.length > 1 ? 's' : ''}</span>}
              </span>
              <span style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {node.badges.map((b) => (
                  <Badge key={b} b={b} />
                ))}
                {node.r37_regression && <Badge b="r37" />}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function DecisionCard({ d }: { d: PromotionDecision }) {
  return (
    <div data-testid="decision" style={{ border: '1px solid var(--line)', borderRadius: 8, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Badge b={d.decision} />
        {!d.deployable && <Badge b="dev-only" />}
        <span data-testid="decision-label" style={{ fontWeight: 700, fontSize: 14 }}>
          {d.label}
        </span>
      </div>
      <div style={{ fontSize: 12, color: 'var(--n1)' }}>
        {MODE_TEXT[d.mode] ?? d.mode} · {d.candidate_version} vs incumbent {d.incumbent_version} · {d.n_proposals} proposals
      </div>
      {d.reasons.length > 0 && <div style={{ fontSize: 13 }}>Reasons: {d.reasons.map((r) => r.replace(/_/g, ' ')).join(', ')}</div>}
      <table style={{ borderCollapse: 'collapse', fontSize: 13, ...TABULAR }}>
        <thead>
          <tr style={{ textAlign: 'left', color: 'var(--n2)' }}>
            <th style={{ padding: '4px 10px 4px 0', fontWeight: 600 }}>Metric</th>
            <th style={{ padding: '4px 10px', fontWeight: 600 }}>Mean delta</th>
            <th style={{ padding: '4px 10px', fontWeight: 600 }}>Interval</th>
            <th style={{ padding: '4px 10px', fontWeight: 600 }}>Noise SD</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(d.deltas).map(([m, x]) => (
            <tr key={m} style={{ borderTop: '1px solid var(--line)' }}>
              <td style={{ padding: '4px 10px 4px 0' }}>{metricName(m)}</td>
              <td style={{ padding: '4px 10px', fontWeight: 700 }}>{fmtDelta(x.mean_delta)}</td>
              <td style={{ padding: '4px 10px' }}>{ciText(x)}</td>
              <td style={{ padding: '4px 10px' }}>{x.noise_sd === null || x.noise_sd === undefined ? '—' : x.noise_sd.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.notes.map((n) => (
        <div key={n} style={{ fontSize: 12, color: 'var(--n1)' }}>
          {n}
        </div>
      ))}
    </div>
  );
}

function PromptDiffs({ versionId }: { versionId: string }) {
  const [open, setOpen] = useState(false);
  const [diff, reload] = useLoad(open && getToken() ? `diff:${versionId}` : null, () => api.candidateDiff(versionId));
  if (!open)
    return (
      <button type="button" onClick={() => setOpen(true)} style={{ ...OUTLINE_BTN, alignSelf: 'flex-start' }}>
        Show prompt diffs
      </button>
    );
  if (diff.status === 'error') return <ErrorCard title="Could not load the prompt diffs" message={diff.error} onRetry={reload} />;
  if (!diff.data) return <LoadingCard title="Loading prompt diffs" />;
  const prompts = Object.entries(diff.data.prompts);
  if (!prompts.length) return <div style={{ fontSize: 13, color: 'var(--n1)' }}>No prompt changed.</div>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <button type="button" onClick={() => setOpen(false)} style={{ ...OUTLINE_BTN, alignSelf: 'flex-start' }}>
        Hide prompt diffs
      </button>
      {prompts.map(([role, text]) => (
        <div key={role} data-prompt-diff={role}>
          <div style={{ font: `700 12px ${FONT}`, marginBottom: 4 }}>{role}</div>
          <pre style={{ margin: 0, padding: '8px 10px', background: 'var(--soft)', borderRadius: 6, overflowX: 'auto', font: `12px/1.5 ${MONO}`, color: 'var(--ink)' }}>
            {text.split('\n').map((line, i) => {
              const k = lineDiffKind(line);
              const bg = k === 'add' ? 'rgba(30,158,106,.16)' : k === 'del' ? 'rgba(229,72,77,.16)' : 'transparent';
              return (
                <div key={i} data-line={k} style={{ background: bg, fontWeight: k === 'hunk' || k === 'meta' ? 700 : 400, whiteSpace: 'pre-wrap' }}>
                  {line || ' '}
                </div>
              );
            })}
          </pre>
        </div>
      ))}
    </div>
  );
}

function CandidatePanel({ d }: { d: CandidateDetail }) {
  const n = d.node;
  const lines = summaryLines(d.summary);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
      <div style={{ ...CARD, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, ...TABULAR }}>{n.version_id}</h2>
          {n.badges.map((b) => (
            <Badge key={b} b={b} />
          ))}
        </div>
        <div style={{ fontSize: 13, color: 'var(--n1)', wordBreak: 'break-word' }}>
          {n.name}
          {n.parent_id ? ` · parent ${n.parent_id}` : ''}
          {n.cycle_id ? ` · ${n.cycle_id}` : ''}
          {n.twins.length ? ` · api twin ${n.twins.join(', ')}` : ''}
        </div>
      </div>

      {d.new_expert && (
        <section data-testid="new-expert" aria-label="New expert" style={{ ...CARD, borderColor: '#8B5CF6', borderWidth: 2, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <h3 style={{ ...LABEL, margin: 0, color: 'var(--ink)' }}>New expert: {d.new_expert.id}</h3>
          <div style={{ fontSize: 13 }}>
            <strong>Domain:</strong> {d.new_expert.domain}
          </div>
          <div style={{ fontSize: 13 }}>
            <strong>Router gloss:</strong> {d.new_expert.router_gloss}
          </div>
          {patternText(d.new_expert.target_pattern) && (
            <div style={{ fontSize: 13 }}>
              <strong>Targeted Failure Memory pattern:</strong> {patternText(d.new_expert.target_pattern)}
            </div>
          )}
          {d.new_expert.rationale && <div style={{ fontSize: 13, color: 'var(--n1)' }}>{d.new_expert.rationale}</div>}
          <details>
            <summary style={{ cursor: 'pointer', font: `700 12px ${FONT}` }}>Prompt</summary>
            <pre style={{ margin: '8px 0 0', padding: '8px 10px', background: 'var(--soft)', borderRadius: 6, whiteSpace: 'pre-wrap', font: `12px/1.5 ${MONO}` }}>{d.new_expert.prompt_text}</pre>
          </details>
        </section>
      )}

      <Section title="Config diff" testId="config-diff">
        {lines.length ? (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, display: 'flex', flexDirection: 'column', gap: 3 }}>
            {lines.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        ) : (
          <div style={{ fontSize: 13, color: 'var(--n1)' }}>{n.origin === 'seed' ? 'The self-evolution base: no diff.' : 'No structural change recorded.'}</div>
        )}
        {d.rationale && !d.new_expert && <div style={{ fontSize: 13, color: 'var(--n1)' }}>Rationale: {d.rationale}</div>}
        {n.origin !== 'seed' && <PromptDiffs key={n.version_id} versionId={n.version_id} />}
      </Section>

      <Section title="Train / val aggregates" testId="metrics">
        {d.metrics.length ? (
          <table style={{ borderCollapse: 'collapse', fontSize: 13, ...TABULAR }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--n2)' }}>
                <th style={{ padding: '4px 10px 4px 0', fontWeight: 600 }}>Split</th>
                <th style={{ padding: '4px 10px', fontWeight: 600 }}>Metric</th>
                <th style={{ padding: '4px 10px', fontWeight: 600 }}>Mean ± noise</th>
                <th style={{ padding: '4px 10px', fontWeight: 600 }}>Judge</th>
              </tr>
            </thead>
            <tbody>
              {d.metrics.flatMap((s) =>
                Object.entries(s.metrics).map(([m, x]) => (
                  <tr key={`${s.split}:${s.judge_version}:${m}`} style={{ borderTop: '1px solid var(--line)' }}>
                    <td style={{ padding: '4px 10px 4px 0' }}>{s.split}</td>
                    <td style={{ padding: '4px 10px' }}>{metricName(m)}</td>
                    <td style={{ padding: '4px 10px', fontWeight: 700 }}>{withNoise(x.mean, x.noise_sd)}</td>
                    <td style={{ padding: '4px 10px', color: 'var(--n1)' }}>{s.judge_version}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        ) : (
          <div style={{ fontSize: 13, color: 'var(--n1)' }}>No full train/val replay recorded yet (womm evolve replay).</div>
        )}
      </Section>

      <Section title="Holdout decision" testId="holdout">
        {d.holdout.decisions.length ? d.holdout.decisions.map((x) => <DecisionCard key={x.gate_id} d={x} />) : <div style={{ fontSize: 13, color: 'var(--n1)' }}>{d.holdout.message}</div>}
      </Section>

      <Section title="R37 diff regression check" testId="r37">
        {d.r37.regression && (
          <div role="note" data-testid="r37-warning" style={{ background: WARN_BG, color: WARN_INK, borderRadius: 6, padding: '8px 10px', fontSize: 13, fontWeight: 600 }}>
            {d.r37.message}
          </div>
        )}
        <div style={{ fontSize: 13, ...TABULAR }}>Coverage on demo_penalties_amended: {r37Line(d.r37)}</div>
        {!d.r37.regression && <div style={{ fontSize: 12, color: 'var(--n1)' }}>{d.r37.message}</div>}
      </Section>
    </div>
  );
}

export function Evolution() {
  const authed = !!getToken();
  const [lineage, reload] = useLoad(authed ? 'evolution:lineage' : null, api.lineage);
  const [picked, setPicked] = useState<string | null>(null);
  const nodes = lineage.data?.nodes ?? [];
  const selected = picked && nodes.some((n) => n.version_id === picked) ? picked : defaultSelection(nodes);
  const [detail, reloadDetail] = useLoad(authed && selected ? `evolution:${selected}` : null, () => api.candidate(selected!));

  if (lineage.status === 'error' && !lineage.data) return <ErrorCard title="Could not load the evolution archive" message={lineage.error} onRetry={reload} />;
  if (!lineage.data) return <LoadingCard title="Loading the evolution archive" sub="Reading the candidate lineage." />;
  if (!nodes.length) return <EmptyCard title="No candidates yet" sub="Run `womm evolve seed` and `womm evolve cycle` to archive candidates." />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <p style={{ fontSize: 14, color: 'var(--n1)', lineHeight: 1.55, margin: 0, maxWidth: '80ch' }}>
        Every candidate the Improvement Planner proposed, with its parent and config diff. Promotion is decided once per cycle on the sealed holdout; the R37 diff check is shown for monitoring and never gates.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: '320px minmax(0,1fr)', gap: 16, alignItems: 'start' }}>
        <section aria-label="Candidates" style={{ ...CARD, padding: '14px 14px', display: 'flex', flexDirection: 'column', gap: 10, position: 'sticky', top: 90 }}>
          <h3 style={{ ...LABEL, margin: 0 }}>Lineage</h3>
          <LineageTree nodes={nodes} selected={selected} onSelect={setPicked} />
          {lineage.data.publish_summary === false && <div style={{ fontSize: 11, color: 'var(--n2)', lineHeight: 1.5 }}>Holdout decisions are not published here (publish_summary is off).</div>}
        </section>
        <div style={{ minWidth: 0 }}>
          {detail.status === 'error' ? (
            <ErrorCard title="Could not load the candidate" message={detail.error} onRetry={reloadDetail} />
          ) : detail.data && detail.data.node.version_id === selected ? (
            <CandidatePanel d={detail.data} />
          ) : (
            <LoadingCard title="Loading the candidate" />
          )}
        </div>
      </div>
      {lineage.status === 'error' && <div style={{ color: RED, fontSize: 12 }}>{lineage.error}</div>}
    </div>
  );
}
