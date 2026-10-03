// Proposal / final-text comparison of the provisions a diff scenario changes (R36 page 2).
// Pure: pairs each change's before/after source texts and diffs them word by word.
import type { ProvisionChange, ScenarioSources } from '../types';
import { versionLabel } from './scenario';

export type DiffOp = 'same' | 'add' | 'del';

export interface Segment {
  op: DiffOp;
  text: string;
}

/**
 * Words (letters and digits), whitespace runs and single punctuation characters. Joining the
 * tokens gives back the input exactly, so every diff below is whitespace-preserving.
 */
export function tokenize(text: string): string[] {
  return text.match(/\s+|[\p{L}\p{N}]+|[^\s\p{L}\p{N}]/gu) ?? [];
}

/**
 * Above this many LCS cells (after trimming the common prefix and suffix) the middle is shown as
 * one replacement instead. Two ~5k-character articles need about 5M cells (Uint16, ~10 MB).
 */
export const MAX_CELLS = 8_000_000;

const isSpace = (s: string) => /^\s+$/.test(s);

function push(out: Segment[], op: DiffOp, text: string): void {
  if (!text) return;
  const last = out[out.length - 1];
  if (last && last.op === op) last.text += text;
  else out.push({ op, text });
}

/**
 * Word-level diff of `before` → `after` as one sequence of segments. Joining the `same` and `del`
 * segments gives `before`; joining `same` and `add` gives `after`. Within each changed stretch the
 * deletion comes before the addition. Deterministic: ties prefer deleting first.
 */
export function diffWords(before: string, after: string): Segment[] {
  if (before === after) return before ? [{ op: 'same', text: before }] : [];
  const a = tokenize(before);
  const b = tokenize(after);

  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;

  // Intern tokens so the LCS compares integers.
  const ids = new Map<string, number>();
  const intern = (t: string) => {
    let id = ids.get(t);
    if (id === undefined) ids.set(t, (id = ids.size));
    return id;
  };
  const A = a.slice(pre, a.length - suf).map(intern);
  const B = b.slice(pre, b.length - suf).map(intern);
  const n = A.length;
  const m = B.length;

  const raw: Segment[] = [];
  push(raw, 'same', a.slice(0, pre).join(''));
  if ((n + 1) * (m + 1) > MAX_CELLS) {
    push(raw, 'del', a.slice(pre, a.length - suf).join(''));
    push(raw, 'add', b.slice(pre, b.length - suf).join(''));
  } else {
    // L[i][j] = LCS length of A[i..] and B[j..]; at most min(n, m) < 2^16 under MAX_CELLS.
    const w = m + 1;
    const L = new Uint16Array((n + 1) * w);
    for (let i = n - 1; i >= 0; i--) {
      const row = i * w;
      const next = row + w;
      const ai = A[i];
      for (let j = m - 1; j >= 0; j--) {
        L[row + j] = ai === B[j] ? L[next + j + 1] + 1 : Math.max(L[next + j], L[row + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && A[i] === B[j]) {
        push(raw, 'same', a[pre + i]);
        i++;
        j++;
      } else if (j >= m || (i < n && L[(i + 1) * w + j] >= L[i * w + j + 1])) {
        push(raw, 'del', a[pre + i]);
        i++;
      } else {
        push(raw, 'add', b[pre + j]);
        j++;
      }
    }
  }
  push(raw, 'same', a.slice(a.length - suf).join(''));
  return cleanup(raw);
}

/**
 * Readability pass: a lone whitespace run kept between two changes is folded into them (so
 * "A B" → "C D" reads as one replacement, not two), and each changed stretch becomes one
 * deletion followed by one addition. Both texts still join back exactly.
 */
function cleanup(raw: Segment[]): Segment[] {
  const out: Segment[] = [];
  let del = '';
  let add = '';
  const flush = () => {
    push(out, 'del', del);
    push(out, 'add', add);
    del = add = '';
  };
  for (let k = 0; k < raw.length; k++) {
    const s = raw[k];
    if (s.op === 'del') del += s.text;
    else if (s.op === 'add') add += s.text;
    else if (k > 0 && k < raw.length - 1 && isSpace(s.text)) {
      del += s.text;
      add += s.text;
    } else {
      flush();
      push(out, 'same', s.text);
    }
  }
  flush();
  return out;
}

/** The segments one column shows: the proposal side drops additions, the final side deletions. */
export const sideOf = (segs: Segment[], side: 'before' | 'after'): Segment[] => segs.filter((s) => s.op !== (side === 'before' ? 'add' : 'del'));

const countWords = (text: string) => tokenize(text).filter((t) => /[\p{L}\p{N}]/u.test(t)).length;

export interface ComparisonSide {
  /** "Art 71". */
  article: string;
  sourceId: string;
  /** "COM(2021) 206". */
  version: string;
  /** "Proposal" / "Final text" from the version, else the version label. */
  stage: string;
  /** Segments for this column; null when the source text is not among the scenario sources. */
  segments: Segment[] | null;
}

export interface ComparisonVM {
  provisionKey: string;
  kind: string;
  /** "Art 71 → Art 99", "— → Art 53" (added), "Art 12 → —" (removed). */
  label: string;
  before: ComparisonSide | null;
  after: ComparisonSide | null;
  /** Words removed from the proposal / added in the final text. */
  removed: number;
  added: number;
  /** Both texts present and word for word the same. */
  identical: boolean;
  /** A side's source text is missing from the scenario sources. */
  missingText: boolean;
}

function stageOf(version: string): string {
  if (/^com/i.test(version)) return 'Proposal';
  if (/^reg/i.test(version)) return 'Final text';
  return versionLabel(version);
}

/** True when at least one change has both a proposal and a final-text side to compare. */
export function hasComparableChanges(changes: ProvisionChange[] | null | undefined): boolean {
  return (changes ?? []).some((c) => c.before && c.after);
}

/** One comparison per provision change, in the API's order. */
export function provisionComparisons(data: ScenarioSources | null | undefined): ComparisonVM[] {
  if (!data) return [];
  const texts = new Map(data.sources.map((s) => [s.source_id, s.text]));
  return data.changes.map((c) => {
    const tb = c.before ? texts.get(c.before.source_id) : undefined;
    const ta = c.after ? texts.get(c.after.source_id) : undefined;
    const missingText = (!!c.before && tb === undefined) || (!!c.after && ta === undefined);
    let segs: Segment[] | null = null;
    if (tb !== undefined && ta !== undefined) segs = diffWords(tb, ta);
    else if (tb !== undefined && !c.after) segs = tb ? [{ op: 'del', text: tb }] : [];
    else if (ta !== undefined && !c.before) segs = ta ? [{ op: 'add', text: ta }] : [];

    const side = (ref: ProvisionChange['before'], which: 'before' | 'after'): ComparisonSide | null => {
      if (!ref) return null;
      const version = ref.source_id.split('/')[0];
      return {
        article: `Art ${ref.article}`,
        sourceId: ref.source_id,
        version: versionLabel(version),
        stage: stageOf(version),
        segments: segs ? sideOf(segs, which) : null,
      };
    };
    const before = side(c.before, 'before');
    const after = side(c.after, 'after');
    const removed = (segs ?? []).filter((s) => s.op === 'del').reduce((n, s) => n + countWords(s.text), 0);
    const added = (segs ?? []).filter((s) => s.op === 'add').reduce((n, s) => n + countWords(s.text), 0);
    return {
      provisionKey: c.provision_key,
      kind: c.kind,
      label: `${before?.article ?? '—'} → ${after?.article ?? '—'}`,
      before,
      after,
      removed,
      added,
      identical: tb !== undefined && ta !== undefined && tb === ta,
      missingText,
    };
  });
}
