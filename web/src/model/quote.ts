// Find an evidence quote inside its source text, tolerating the same differences the backend's
// citation check forgives (src/womm/citations.py): NFKC, case, quote/dash styles, soft hyphens,
// line-break hyphenation, runs of whitespace and "..." elisions.

const CHAR_MAP: Record<string, string> = {
  '‘': "'", '’': "'", '‚': "'", '‛': "'", '′': "'",
  '“': '"', '”': '"', '„': '"', '‟': '"', '«': '"', '»': '"',
  '‐': '-', '‑': '-', '‒': '-', '–': '-', '—': '-', '―': '-', '−': '-',
  '­': '',
};

const isWord = (c: string | undefined) => !!c && /[\p{L}\p{N}_]/u.test(c);
const isSpace = (c: string) => /\s/.test(c);

export interface Normalized {
  text: string;
  /** For each char of `text`, the index of the original char it came from. */
  map: number[];
}

export function normalizeWithMap(src: string): Normalized {
  const out: string[] = [];
  const map: number[] = [];
  let pendingSpace = -1;
  const chars = Array.from(src);
  // Original UTF-16 offsets of each code point.
  const offs: number[] = [];
  let o = 0;
  for (const ch of chars) {
    offs.push(o);
    o += ch.length;
  }
  for (let i = 0; i < chars.length; i++) {
    const raw = chars[i];
    // Line-break hyphenation: "regu-\n  lation" → "regulation".
    if ((raw === '-' || CHAR_MAP[raw] === '-') && isWord(chars[i - 1])) {
      let j = i + 1;
      while (j < chars.length && (chars[j] === ' ' || chars[j] === '\t')) j++;
      if (chars[j] === '\n' || chars[j] === '\r') {
        let k = j;
        while (k < chars.length && isSpace(chars[k])) k++;
        if (isWord(chars[k])) {
          i = k - 1;
          continue;
        }
      }
    }
    if (isSpace(raw)) {
      if (pendingSpace < 0) pendingSpace = offs[i];
      continue;
    }
    const mapped = raw in CHAR_MAP ? CHAR_MAP[raw] : raw.normalize('NFKC').toLowerCase();
    if (!mapped) continue;
    if (pendingSpace >= 0) {
      if (out.length) {
        out.push(' ');
        map.push(pendingSpace);
      }
      pendingSpace = -1;
    }
    for (const m of mapped) {
      if (isSpace(m)) continue;
      out.push(m);
      map.push(offs[i]);
    }
  }
  return { text: out.join(''), map };
}

const EDGE = new Set([' ', '\t', '.', ',', ';', ':', '!', '?', '"', "'", '(', ')', '[', ']']);
const trimEdge = (s: string) => {
  let a = 0;
  let b = s.length;
  while (a < b && EDGE.has(s[a])) a++;
  while (b > a && EDGE.has(s[b - 1])) b--;
  return s.slice(a, b);
};

export function quoteSegments(quote: string): string[] {
  return normalizeWithMap(quote)
    .text.split(/\[?\s*\.{3,}\s*\]?/)
    .map(trimEdge)
    .filter(Boolean);
}

export interface Highlight {
  found: boolean;
  before: string;
  quote: string;
  after: string;
}

/** Split `text` around the passage `quote` points at. Elided quotes highlight first to last segment. */
export function highlightQuote(text: string, quote: string): Highlight {
  const segs = quoteSegments(quote);
  const hay = normalizeWithMap(text);
  if (!segs.length) return { found: false, before: text, quote: '', after: '' };
  let pos = 0;
  let start = -1;
  let endN = -1;
  for (const seg of segs) {
    const at = hay.text.indexOf(seg, pos);
    if (at < 0) return { found: false, before: text, quote: '', after: '' };
    if (start < 0) start = at;
    endN = at + seg.length - 1;
    pos = at + seg.length;
  }
  const a = hay.map[start];
  const lastOrig = hay.map[endN];
  const b = lastOrig + (text.codePointAt(lastOrig)! > 0xffff ? 2 : 1);
  return { found: true, before: text.slice(0, a), quote: text.slice(a, b), after: text.slice(b) };
}
