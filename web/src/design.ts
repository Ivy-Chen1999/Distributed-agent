// Design constants carried over from web/design/WOMM Console.dc.html.
import type { CSSProperties } from 'react';

export interface AgentStyle {
  n: string;
  c: string;
}

export const AG: Record<string, AgentStyle> = {
  diff: { n: 'Regulatory diff', c: '#8FA3A6' },
  planner: { n: 'Impact Planner', c: '#64748B' },
  router: { n: 'Router · Jev', c: '#C9A227' },
  legal: { n: 'Legal', c: '#5B5BD6' },
  fiscal: { n: 'Fiscal', c: '#E0A020' },
  stakeholder: { n: 'Stakeholder', c: '#FF8064' },
  workforce: { n: 'Workforce', c: '#4F9D69' },
  board: { n: 'Impact Board', c: '#00C4CC' },
  citation: { n: 'Citation check', c: '#00C4CC' },
  synthesis: { n: 'Synthesis', c: '#8B5CF6' },
  dossier: { n: 'Impact Dossier', c: '#8FA3A6' },
};

const EXTRA_COLOURS = ['#0E9AA7', '#D6409F', '#7C8F2E', '#B5651D', '#3B82F6'];

/** Style for any node id; unknown experts get a stable colour and a title-cased name. */
export function ag(id: string): AgentStyle {
  if (AG[id]) return AG[id];
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const name = id.replace(/[_-]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
  return { n: name, c: EXTRA_COLOURS[h % EXTRA_COLOURS.length] };
}

export interface Theme {
  bg: string;
  card: string;
  soft: string;
  line: string;
  ink: string;
  n1: string;
  n2: string;
  accInk: string;
  accSoft: string;
  side: string;
  hl: string;
}

export const TH: Record<'light' | 'dark', Theme> = {
  light: { bg: '#F3F6F6', card: '#FFFFFF', soft: '#EDF2F2', line: '#DCE4E4', ink: '#0F0F0F', n1: '#4A565A', n2: '#626E71', accInk: '#00737A', accSoft: '#D6F5F6', side: '#0F0F0F', hl: '#C4F1F3' },
  dark: { bg: '#0A1011', card: '#111A1C', soft: '#182326', line: '#253235', ink: '#EDF3F3', n1: '#A9B5B7', n2: '#8A9799', accInk: '#3ED9E0', accSoft: '#0C3033', side: '#050809', hl: '#0F4A4E' },
};

export type ScreenId = 'overview' | 'pipeline' | 'detail' | 'agents' | 'topo' | 'chat' | 'settings';

export const ICON: Record<ScreenId, string> = {
  overview: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z',
  pipeline: 'M3 3h6v6H3zM15 15h6v6h-6zM6 9v3a3 3 0 0 0 3 3h6',
  detail: 'M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h5',
  agents: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8',
  topo: 'M12 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4M5 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4M19 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4M12 7v5M12 12l-5.5 5.5M12 12l5.5 5.5',
  chat: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  settings: 'M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6',
};

export const TITLES: Record<ScreenId, string> = {
  overview: 'Overview',
  pipeline: 'Run pipeline',
  detail: 'Run detail',
  agents: 'Agents',
  topo: 'Topology',
  chat: 'Ask WOMM',
  settings: 'Settings',
};

export const SCREENS = Object.keys(TITLES) as ScreenId[];

export type NodeStatus = 'queued' | 'running' | 'done' | 'failed' | 'skipped';

export const ST: Record<NodeStatus, { t: string; c: string }> = {
  queued: { t: 'Queued', c: '#8A9699' },
  running: { t: 'Running', c: '#2F80ED' },
  done: { t: 'Done', c: '#1E9E6A' },
  failed: { t: 'Failed', c: '#E5484D' },
  skipped: { t: 'Skipped', c: '#8A9699' },
};

export function statusText(s: NodeStatus, errorKind?: string | null): string {
  return s === 'failed' && errorKind ? `Failed · ${errorKind}` : ST[s].t;
}

export const RED = '#E5484D';
export const GREEN = '#1E9E6A';
export const AMBER = '#E0A020';
export const BLUE = '#2F80ED';
export const ACC = '#00C4CC';
export const PULSE = 'wpulse 1.2s ease-out infinite';
export const WARN_BG = '#FFF1CC';
export const WARN_INK = '#8A5A00';

export const MOON = 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z';
export const SUN = 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4';

export const FONT = "'Manrope',sans-serif";
export const TABULAR: CSSProperties = { fontVariantNumeric: 'tabular-nums' };

/** The recurring uppercase section label. */
export const LABEL: CSSProperties = {
  font: `700 11px/1.4 ${FONT}`,
  letterSpacing: '.08em',
  textTransform: 'uppercase',
  color: 'var(--n2)',
};

export const CARD: CSSProperties = {
  background: 'var(--card)',
  border: '1px solid var(--line)',
  borderRadius: 12,
};

export const fmtS = (s: number): string => {
  if (!Number.isFinite(s) || s < 0) s = 0;
  if (s < 60) return s.toFixed(1) + 's';
  const r = Math.round(s);
  return Math.floor(r / 60) + 'm ' + String(r % 60).padStart(2, '0') + 's';
};

export const kTok = (n: number): string => (n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n));
