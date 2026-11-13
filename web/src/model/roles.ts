// The console's two interfaces (docs/ui/README.md, Interfaces): "the law", for people who study a
// regulation (an impact analyst, an implementer, a reader), and "the system", for people who
// study WOMM itself (the team, an evaluator). The choice is kept in this browser; it is a view,
// not access control: the API still accepts any request with the token.
import { TITLES, type ScreenId } from '../design';

export type RoleId = 'law' | 'system';

export interface Role {
  id: RoleId;
  name: string;
  /** One line under the switch: what this interface is for. */
  description: string;
  /** Screens in sidebar order; a subset of SCREENS. */
  screens: ScreenId[];
  home: ScreenId;
  /** Shows the system-version chip, staged overrides and Settings (team tooling). */
  tooling: boolean;
  /** May start runs (each run costs LLM calls and adds to the run history). */
  canRun: boolean;
  /** Shows the run status badge and the header Run button. */
  runMachinery: boolean;
  /** Shows a run as a plain analysis: no run ids, system versions, pipeline status or event log. */
  plainAnalysis?: boolean;
  /** Screen names that differ in this interface (Run detail reads as "Analysis" in the law). */
  titles?: Partial<Record<ScreenId, string>>;
}

export const ROLES: Role[] = [
  {
    id: 'law',
    name: 'The law',
    description: 'What changed in a regulation, for whom, and what the analyses found.',
    screens: ['regs', 'act', 'detail'],
    home: 'regs',
    tooling: false,
    canRun: true,
    runMachinery: false,
    plainAnalysis: true,
    titles: { detail: 'Analysis' },
  },
  {
    id: 'system',
    name: 'The system',
    description: 'How WOMM produced its analyses: runs, agents, evaluation and settings.',
    screens: ['overview', 'pipeline', 'detail', 'agents', 'topo', 'evolution', 'settings'],
    home: 'overview',
    tooling: true,
    canRun: true,
    runMachinery: true,
  },
];

/**
 * The system interface is the default while the console is a team tool. Outside users (a
 * read-only API token, not built yet) would only ever get the law.
 */
export const DEFAULT_ROLE: RoleId = 'system';
export const ROLE_KEY = 'womm.role';

/** Values stored by earlier versions of the console, which had five view presets. */
const RENAMED: Record<string, RoleId> = { full: 'system', evaluator: 'system', reader: 'law', analyst: 'law', implementer: 'law', municipal: 'law' };

export function roleOf(id: string | null | undefined): Role {
  const want = id && RENAMED[id] ? RENAMED[id] : id;
  return ROLES.find((r) => r.id === want) ?? ROLES.find((r) => r.id === DEFAULT_ROLE)!;
}

export const canSee = (role: Role, screen: ScreenId): boolean => role.screens.includes(screen);

/** The interface that has `screen` (the current one first, since Run detail is in both). */
export const roleWith = (current: Role, screen: ScreenId): Role => (canSee(current, screen) ? current : (ROLES.find((r) => canSee(r, screen)) ?? current));

/** The screen's name in this interface. */
export const screenTitle = (role: Role, screen: ScreenId): string => role.titles?.[screen] ?? TITLES[screen];

/** Where an interface lands when it asks for `screen`: the screen itself if it has it, else home. */
export const screenFor = (role: Role, screen: ScreenId): ScreenId => (canSee(role, screen) ? screen : role.home);

/** After starting a run: the live pipeline if the interface has it, else the analysis. */
export const afterRunScreen = (role: Role): ScreenId => (canSee(role, 'pipeline') ? 'pipeline' : 'detail');

export function loadRole(): Role {
  try {
    return roleOf(localStorage.getItem(ROLE_KEY));
  } catch {
    return roleOf(null);
  }
}

export function saveRole(id: RoleId): void {
  try {
    localStorage.setItem(ROLE_KEY, id);
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
}
