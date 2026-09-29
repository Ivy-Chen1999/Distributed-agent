// Human labels derived from Scenario records and the provision changes of a scenario.
import type { ProvisionChange, Scenario } from '../types';

const ACRONYMS: Record<string, string> = { sme: 'SME', smes: 'SMEs', ai: 'AI', eu: 'EU', ia: 'IA' };

export function humanize(slug: string): string {
  const words = slug.split(/[_\s-]+/).filter(Boolean).map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase());
  if (!words.length) return slug;
  const first = words[0];
  words[0] = first === first.toUpperCase() ? first : first[0].toUpperCase() + first.slice(1);
  return words.join(' ');
}

/** "eval_sme_impacts" → "SME impacts". */
export function scenarioName(id: string): string {
  return humanize(id.replace(/^(eval|demo)_/, ''));
}

/** "com2021_206" → "COM(2021) 206"; "reg2024_1689" → "Regulation (EU) 2024/1689". */
export function versionLabel(v: string | null | undefined): string {
  if (!v) return '—';
  let m = /^com(\d{4})_(\d+)$/i.exec(v);
  if (m) return `COM(${m[1]}) ${m[2]}`;
  m = /^reg(\d{4})_(\d+)$/i.exec(v);
  if (m) return `Regulation (EU) ${m[1]}/${m[2]}`;
  return v;
}

const shortVersion = (v: string) => versionLabel(v).replace('Regulation (EU) ', 'Reg ');

const REGULATIONS: Record<string, string> = { ai_act: 'EU AI Act' };

/** "EU AI Act · proposal" from the provision keys and the after-version. */
export function regulationTitle(sc: Scenario | null | undefined): string {
  if (!sc) return 'Regulation';
  const prefix = sc.provision_keys[0]?.split('/')[0] ?? '';
  const name = REGULATIONS[prefix] ?? humanize(prefix || sc.after_version);
  const stage = /^com/i.test(sc.after_version) ? 'proposal' : /^reg/i.test(sc.after_version) ? 'adopted text' : sc.after_version;
  return sc.before_version ? `${name} · ${/^com/i.test(sc.before_version) ? 'proposal' : shortVersion(sc.before_version)} → ${stage}` : `${name} · ${stage}`;
}

/** Collapse article numbers into ranges: [53,54,55,71] → "53–55, 71". */
export function articleRanges(articles: string[]): string {
  const nums = [...new Set(articles)];
  const ints = nums.map((a) => (/^\d+$/.test(a) ? Number(a) : NaN));
  if (ints.some((n) => Number.isNaN(n))) return nums.join(', ');
  const sorted = [...new Set(ints)].sort((a, b) => a - b);
  const out: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    out.push(j - i >= 2 ? `${sorted[i]}–${sorted[j]}` : j > i ? `${sorted[i]}, ${sorted[j]}` : `${sorted[i]}`);
    i = j + 1;
  }
  return out.join(', ');
}

/** "COM(2021) 206 · Art 53–55, 71" (or "before → after" for a two-version diff). */
export function regulationSub(sc: Scenario | null | undefined, changes: ProvisionChange[] | null | undefined): string {
  if (!sc) return '';
  const after = (changes ?? []).map((c) => c.after?.article).filter((a): a is string => !!a);
  const arts = after.length ? ` · Art ${articleRanges(after)}` : '';
  const doc = sc.before_version ? `${shortVersion(sc.before_version)} → ${shortVersion(sc.after_version)}` : versionLabel(sc.after_version);
  return doc + arts;
}

/** "Evaluation · IA §6.1.4" or "Demo diff". */
export function scenarioKind(sc: Scenario | null | undefined): string {
  if (!sc) return '';
  if (sc.kind === 'demo') return 'Demo diff';
  const sec = sc.ia_reference && /section\s+([\d.]+)/i.exec(sc.ia_reference);
  return sec ? `Evaluation · IA §${sec[1]}` : humanize(sc.kind);
}

/** Headline: the scenario's own question, else a generic one. */
export function scenarioHeadline(sc: Scenario | null | undefined): string {
  if (!sc) return 'No scenario selected';
  const q = sc.description.split(/(?<=[.?!])\s+/).find((s) => s.trim().endsWith('?'));
  return q?.trim() ?? `What changes: ${scenarioName(sc.scenario_id)}`;
}

/** Last provision-key segment as a readable area, "ai_act/innovation/sme_measures" → "SME measures". */
export function areaLabel(provisionKey: string): string {
  const last = provisionKey.split('/').pop() ?? provisionKey;
  return humanize(last);
}

export function joinWords(items: string[]): string {
  if (items.length <= 1) return items.join('');
  return items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
}

const NUM_WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];
export const numWord = (n: number) => NUM_WORDS[n] ?? String(n);

/** "Four provisions changed: …. Three expert agents read them in parallel and posted to one shared board." */
export function scenarioBlurb(sc: Scenario | null | undefined, changes: ProvisionChange[] | null | undefined, experts: number): string {
  if (!sc) return '';
  const keys = changes?.length ? changes.map((c) => c.provision_key) : sc.provision_keys;
  const lowerFirst = (a: string) => {
    const w = a.split(' ')[0];
    return w === w.toUpperCase() ? a : a[0].toLowerCase() + a.slice(1);
  };
  const areas = keys.map((k) => lowerFirst(areaLabel(k)));
  const lead = `${numWord(keys.length)} provision${keys.length === 1 ? '' : 's'} changed: ${joinWords(areas)}.`;
  const tail = experts ? ` ${numWord(experts)} expert agent${experts === 1 ? '' : 's'} read them in parallel and posted to one shared board.` : '';
  return lead + tail;
}

export function changeFor(pk: string, changes: ProvisionChange[] | null | undefined): ProvisionChange | undefined {
  return (changes ?? []).find((c) => c.provision_key === pk);
}

/** "Art 53" for a provision key, from the scenario's changes (after, else before). */
export function articleLabel(pk: string, changes: ProvisionChange[] | null | undefined): string {
  const c = changeFor(pk, changes);
  const art = c?.after?.article ?? c?.before?.article;
  return art ? `Art ${art}` : areaLabel(pk);
}
