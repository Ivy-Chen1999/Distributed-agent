// Overview: the latest published evaluation of the system version this server runs.
import { api, getToken } from '../api';
import { useConsole } from '../ctx';
import { CARD, FONT, LABEL, TABULAR } from '../design';
import { useLoad } from '../hooks';
import { evalBasis, evalRows } from '../model/evaluation';
import { fmtDate } from '../model/overview';
import { HButton, OUTLINE_BTN } from './ui';

export function EvalCard() {
  const C = useConsole();
  const version = C.system.data?.version_id ?? null;
  const [report, reload] = useLoad(version && getToken() ? `eval:${version}` : null, () => api.latestEval(version ?? ''));

  const head = (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
      <div style={LABEL}>Evaluation of this system version</div>
      {version && <div style={{ font: `500 12px ${FONT}`, ...TABULAR, color: 'var(--n2)' }}>{version}</div>}
    </div>
  );
  let body;
  if (report.status === 'error' && report.code === 404) {
    body = <div style={{ fontSize: 14, color: 'var(--n1)', marginTop: 8 }}>No evaluation published for this version.</div>;
  } else if (report.status === 'error') {
    body = (
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, color: 'var(--n1)' }}>Could not load the evaluation: {report.error}</span>
        <HButton onClick={reload} style={OUTLINE_BTN}>
          Retry
        </HButton>
      </div>
    );
  } else if (report.status !== 'ready' || !report.data) {
    body = <div style={{ fontSize: 14, color: 'var(--n2)', marginTop: 8 }}>Loading the evaluation…</div>;
  } else {
    const r = report.data;
    body = (
      <>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(170px,1fr))', gap: 12, marginTop: 12 }}>
          {evalRows(r).map((row) => (
            <div key={row.name} title={row.help} style={{ minWidth: 0 }}>
              <div style={{ fontSize: 13, color: 'var(--n1)' }}>{row.name}</div>
              <div style={{ fontSize: 22, fontWeight: 600, ...TABULAR, marginTop: 4 }}>{row.value}</div>
              {row.range && <div style={{ fontSize: 12, color: 'var(--n2)', ...TABULAR }}>{row.range}</div>}
            </div>
          ))}
        </div>
        <div style={{ fontSize: 13, color: 'var(--n1)', marginTop: 12, lineHeight: 1.5 }}>
          {evalBasis(r)}, published {fmtDate(r.created_at)}. Scores come from preset reference cases; a whole-act analysis is not scored.
          {r.experiment_url && (
            <>
              {' '}
              <a href={r.experiment_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--ink)', fontWeight: 700 }}>
                Open the experiment in LangSmith
              </a>
            </>
          )}
        </div>
      </>
    );
  }
  return (
    <section aria-label="Evaluation" data-testid="eval-card" style={{ ...CARD, padding: '14px 16px' }}>
      {head}
      {body}
    </section>
  );
}
