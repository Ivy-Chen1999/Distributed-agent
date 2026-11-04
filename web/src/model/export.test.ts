import { describe, expect, it } from 'vitest';
import { dossierToHtml, dossierToMarkdown } from './export';
import { degradedRun, sampleRun } from '../test/fixtures';

describe('dossierToMarkdown', () => {
  const run = sampleRun();
  const d = run.dossier!;
  const md = dossierToMarkdown(run, 'SME impacts');

  it('starts with the title and the run identity', () => {
    expect(md.startsWith('# SME impacts\n')).toBe(true);
    expect(md).toContain(`\`${run.run_id}\``);
    expect(md).toContain(`\`${run.system_version}\``);
  });

  it('counts the dossier in the summary', () => {
    expect(md).toContain(`- ${d.impacts.length} impacts`);
    expect(md).toContain(`- ${d.open_questions.length} open questions`);
    expect(md).toContain(`- ${d.disagreements.length} disagreement`);
  });

  it('lists every impact and every finding', () => {
    for (const imp of d.impacts) expect(md).toContain(`### ${imp.impact_id}:`);
    const findings = d.impacts.reduce((n, imp) => n + imp.findings.length, 0);
    expect(md.match(/^- \*\*/gm)).toHaveLength(findings);
  });

  it('quotes each evidence quote under its source id', () => {
    for (const imp of d.impacts)
      for (const f of imp.findings)
        for (const e of f.evidence) {
          expect(md).toContain(`(\`${e.source_id}\`):`);
          expect(md).toContain(`> ${e.quote.split(/\r?\n/)[0].trim()}`);
        }
  });

  it('keeps multi-line quotes inside the blockquote', () => {
    const f = d.impacts[0].findings[0];
    const multi = { ...run, dossier: { ...d, impacts: [{ ...d.impacts[0], findings: [{ ...f, evidence: [{ ...f.evidence[0], quote: 'first line\nsecond line' }] }] }] } };
    const out = dossierToMarkdown(multi, 'x');
    expect(out).toContain('> first line\n    > second line');
  });

  it('adds failed experts and unresolved questions for a degraded run', () => {
    const out = dossierToMarkdown(degradedRun(), 'Degraded');
    expect(out).toContain('## Failed experts');
    expect(out).toContain('**workforce**: timeout');
    expect(out).toContain('evidence unresolved');
  });

  it('returns an empty string when the run has no dossier', () => {
    expect(dossierToMarkdown({ ...run, dossier: null }, 'x')).toBe('');
  });

  it('ends with a single newline', () => {
    expect(md.endsWith('\n')).toBe(true);
    expect(md.endsWith('\n\n')).toBe(false);
  });
});

describe('dossierToHtml', () => {
  const run = sampleRun();
  const d = run.dossier!;
  const html = dossierToHtml(run, 'SME impacts');

  it('is a complete HTML document with the title', () => {
    expect(html.startsWith('<!doctype html>')).toBe(true);
    expect(html).toContain('<title>SME impacts</title>');
    expect(html).toContain('<h1>SME impacts</h1>');
  });

  it('has every impact, finding and evidence quote', () => {
    for (const imp of d.impacts) expect(html).toContain(`<h3>${imp.impact_id}:`);
    const findings = d.impacts.reduce((n, imp) => n + imp.findings.length, 0);
    expect(html.match(/<div class="finding">/g)).toHaveLength(findings);
    const quotes = d.impacts.reduce((n, imp) => n + imp.findings.reduce((m, f) => m + f.evidence.length, 0), 0);
    expect(html.match(/<blockquote>/g)).toHaveLength(quotes);
  });

  it('escapes model-generated text, so hostile markup stays inert', () => {
    const evil = sampleRun();
    const f = evil.dossier!.impacts[0].findings[0];
    f.impact = '<script>alert(1)</script> & "x"';
    f.evidence[0].quote = '<img src=x onerror=alert(1)>\nline2';
    f.evidence[0].source_id = '"><svg onload=alert(1)>';
    evil.dossier!.impacts[0].summary = '</h3><script>steal()</script>';
    const out = dossierToHtml(evil, '<b>title</b>');
    expect(out).not.toMatch(/<script>alert|<img src=x|<svg onload|<script>steal/i);
    expect(out).toContain('&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;x&quot;');
    expect(out).toContain('&lt;img src=x onerror=alert(1)&gt;<br>line2');
    expect(out).toContain('<title>&lt;b&gt;title&lt;/b&gt;</title>');
  });

  it('includes failed experts of a degraded run', () => {
    expect(dossierToHtml(degradedRun(), 'D')).toContain('<h2>Failed experts</h2>');
  });

  it('returns an empty string when the run has no dossier', () => {
    expect(dossierToHtml({ ...run, dossier: null }, 'x')).toBe('');
  });
});
