// Impact Dossier as a Markdown document, for "Copy as Markdown" and "Download .md".
// Pure and self-contained (types only), so it can be tested without the UI.
import type { ImpactFinding, RunDetail } from '../types';

/** Prefix every line, so multi-line quotes stay inside their blockquote. */
const quoted = (text: string) =>
  text
    .trim()
    .split(/\r?\n/)
    .map((l) => `> ${l}`)
    .join('\n');

const oneLine = (text: string) => text.replace(/\s+/g, ' ').trim();

function findingLines(f: ImpactFinding): string[] {
  const lines = [`- **${f.agent}** · \`${f.provision_key}\` · confidence ${f.confidence.toFixed(2)}`];
  lines.push(`  - Affected actor: ${oneLine(f.affected_actor)}`);
  lines.push(`  - Impact: ${oneLine(f.impact)}`);
  lines.push(`  - Mechanism: ${oneLine(f.mechanism)}`);
  for (const e of f.evidence) {
    lines.push(`  - Evidence (\`${e.source_id}\`):`);
    lines.push(...quoted(e.quote).split('\n').map((l) => `    ${l}`));
  }
  return lines;
}

/**
 * The run's dossier as Markdown: summary counts, every impact with its findings and evidence
 * quotes, impact chains, disagreements, open questions, failed experts and notes.
 * Returns an empty string when the run has no dossier.
 */
export function dossierToMarkdown(run: RunDetail, title: string): string {
  const d = run.dossier;
  if (!d) return '';

  const out: string[] = [];
  const push = (...lines: string[]) => out.push(...lines);

  push(`# ${title}`, '');
  push(`Run \`${run.run_id}\` · system version \`${run.system_version}\` · status ${d.status}`, '');
  if (run.grounding) push(`Evidence grounding: ${run.grounding.passed} of ${run.grounding.total} quotes found verbatim in the sources.`, '');

  push('## Summary', '');
  push(
    `- ${d.impacts.length} impact${d.impacts.length === 1 ? '' : 's'}`,
    `- ${d.open_questions.length} open question${d.open_questions.length === 1 ? '' : 's'}`,
    `- ${d.disagreements.length} disagreement${d.disagreements.length === 1 ? '' : 's'}`,
    `- ${d.failed_experts.length} failed expert${d.failed_experts.length === 1 ? '' : 's'}`,
    '',
  );

  push('## Impacts', '');
  if (!d.impacts.length) push('No impacts.', '');
  for (const imp of d.impacts) {
    push(`### ${imp.impact_id}: ${oneLine(imp.summary)}`, '');
    for (const f of imp.findings) push(...findingLines(f));
    push('');
  }

  if (d.chains.length) {
    push('## Impact chains', '');
    d.chains.forEach((c, i) => push(`${i + 1}. ${c.impact_ids.join(' → ')}: ${oneLine(c.description)}`));
    push('');
  }

  if (d.disagreements.length) {
    push('## Disagreements', '');
    for (const dg of d.disagreements) push(`- ${oneLine(dg.note)} (findings: ${dg.finding_ids.join(', ')})`);
    push('');
  }

  if (d.open_questions.length) {
    push('## Open questions', '');
    for (const q of d.open_questions) {
      const why = q.reason === 'evidence_unresolved' ? 'evidence unresolved' : 'raised in synthesis';
      push(`- ${oneLine(q.question)} (${why}${q.finding_id ? `, finding ${q.finding_id}` : ''})`);
    }
    push('');
  }

  if (d.failed_experts.length) {
    push('## Failed experts', '');
    for (const x of d.failed_experts) push(`- **${x.agent}**: ${x.error_kind}. ${oneLine(x.message)}`);
    push('');
  }

  if (d.notes.length) {
    push('## Notes', '');
    for (const n of d.notes) push(`- ${oneLine(n)}`);
    push('');
  }

  return out.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd() + '\n';
}

/** Escape model-generated text for HTML. The dossier is LLM output, so it is never inserted raw. */
const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const PRINT_CSS = `
@page { margin: 18mm; }
* { box-sizing: border-box; }
body { font: 11pt/1.5 system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; color: #111; margin: 0; }
h1 { font-size: 20pt; margin: 0 0 4pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; padding-bottom: 3pt; border-bottom: 1px solid #ccc; break-after: avoid; }
h3 { font-size: 12pt; margin: 14pt 0 6pt; break-after: avoid; }
p, ul, ol { margin: 4pt 0; }
.meta { color: #555; font-size: 9.5pt; }
.finding { margin: 6pt 0 10pt; padding-left: 10pt; border-left: 3px solid #999; break-inside: avoid; }
dl { margin: 2pt 0 0; }
dt { font-weight: 600; font-size: 9.5pt; color: #444; margin-top: 4pt; }
dd { margin: 0; }
blockquote { margin: 4pt 0; padding: 4pt 8pt; background: #f3f3f3; font-size: 10pt; break-inside: avoid; }
cite { display: block; font-style: normal; font-size: 8.5pt; color: #555; margin-top: 2pt; }
code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 9pt; }
`;

/**
 * The dossier as a self-contained, print-styled HTML document, for "Save as PDF" (the browser's
 * print dialog turns it into a PDF). Same content as {@link dossierToMarkdown}. All text is escaped.
 * Returns an empty string when the run has no dossier.
 */
export function dossierToHtml(run: RunDetail, title: string): string {
  const d = run.dossier;
  if (!d) return '';

  const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`;
  const line = (text: string) => esc(oneLine(text));
  const h: string[] = [];

  h.push(`<h1>${esc(title)}</h1>`);
  h.push(`<p class="meta">Run <code>${esc(run.run_id)}</code> · system version <code>${esc(run.system_version)}</code> · status ${esc(d.status)}</p>`);
  if (run.grounding) h.push(`<p class="meta">Evidence grounding: ${run.grounding.passed} of ${run.grounding.total} quotes found verbatim in the sources.</p>`);

  h.push('<h2>Summary</h2>');
  h.push(
    `<ul><li>${count(d.impacts.length, 'impact')}</li><li>${count(d.open_questions.length, 'open question')}</li><li>${count(d.disagreements.length, 'disagreement')}</li><li>${count(d.failed_experts.length, 'failed expert')}</li></ul>`,
  );

  h.push('<h2>Impacts</h2>');
  if (!d.impacts.length) h.push('<p>No impacts.</p>');
  for (const imp of d.impacts) {
    h.push(`<h3>${esc(imp.impact_id)}: ${line(imp.summary)}</h3>`);
    for (const f of imp.findings) {
      h.push('<div class="finding">');
      h.push(`<p><strong>${esc(f.agent)}</strong> · <code>${esc(f.provision_key)}</code> · confidence ${f.confidence.toFixed(2)}</p>`);
      h.push(`<dl><dt>Affected actor</dt><dd>${line(f.affected_actor)}</dd><dt>Impact</dt><dd>${line(f.impact)}</dd><dt>Mechanism</dt><dd>${line(f.mechanism)}</dd></dl>`);
      for (const e of f.evidence) {
        h.push(`<blockquote>${esc(e.quote.trim()).replace(/\r?\n/g, '<br>')}<cite>Source: <code>${esc(e.source_id)}</code></cite></blockquote>`);
      }
      h.push('</div>');
    }
  }

  if (d.chains.length) {
    h.push('<h2>Impact chains</h2><ol>');
    for (const c of d.chains) h.push(`<li>${esc(c.impact_ids.join(' → '))}: ${line(c.description)}</li>`);
    h.push('</ol>');
  }

  if (d.disagreements.length) {
    h.push('<h2>Disagreements</h2><ul>');
    for (const dg of d.disagreements) h.push(`<li>${line(dg.note)} (findings: ${esc(dg.finding_ids.join(', '))})</li>`);
    h.push('</ul>');
  }

  if (d.open_questions.length) {
    h.push('<h2>Open questions</h2><ul>');
    for (const q of d.open_questions) {
      const why = q.reason === 'evidence_unresolved' ? 'evidence unresolved' : 'raised in synthesis';
      h.push(`<li>${line(q.question)} (${why}${q.finding_id ? `, finding ${esc(q.finding_id)}` : ''})</li>`);
    }
    h.push('</ul>');
  }

  if (d.failed_experts.length) {
    h.push('<h2>Failed experts</h2><ul>');
    for (const x of d.failed_experts) h.push(`<li><strong>${esc(x.agent)}</strong>: ${esc(x.error_kind)}. ${line(x.message)}</li>`);
    h.push('</ul>');
  }

  if (d.notes.length) {
    h.push('<h2>Notes</h2><ul>');
    for (const n of d.notes) h.push(`<li>${line(n)}</li>`);
    h.push('</ul>');
  }

  return `<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>${esc(title)}</title><style>${PRINT_CSS}</style></head><body>\n${h.join('\n')}\n</body></html>\n`;
}
