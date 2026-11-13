// State and actions shared by every screen.
import { createContext, useContext } from 'react';
import type { ScreenId, Theme } from './design';
import type { Loadable } from './hooks';
import type { GroupMode } from './model/dossier';
import type { Role, RoleId } from './model/roles';
import type { ActTarget } from './model/regulations';
import type { RunView } from './model/pipeline';
import type { RunFilter } from './model/runFilter';
import type { RunDetail, RunSummary, Scenario, ScenarioSources, SystemInfo } from './types';

export type DetailTab = 'impacts' | 'chains' | 'disagree' | 'questions' | 'costs' | 'log';

export interface Staged {
  backends: Record<string, string>;
  routerMode: 'shadow' | 'active' | null;
}

export interface ConsoleCtx {
  t: Theme;
  screen: ScreenId;
  go: (screen: ScreenId, extra?: { tab?: DetailTab; sel?: string; openImp?: string | null }) => void;

  system: Loadable<SystemInfo>;
  reloadSystem: () => void;
  scenarios: Loadable<Scenario[]>;
  runs: Loadable<{ runs: RunSummary[] }>;
  reloadRuns: () => void;
  /** How many of the newest runs are loaded, and a way to load a page more. */
  runLimit: number;
  showMoreRuns: () => void;
  /** The Overview runs-table filters; kept in the URL hash. */
  runFilter: RunFilter;
  setRunFilter: (f: RunFilter) => void;
  /** The Act screen's comparison (`before->after`), or null for the newest; kept in the URL hash. */
  actComparison: string | null;
  setActComparison: (key: string | null) => void;

  runId: string | null;
  /** The run the user picked (null while the console falls back to the newest run). */
  pickedRunId: string | null;
  selectRun: (runId: string) => void;
  run: RunDetail | null;
  runLoading: boolean;
  runError: string | null;
  reloadRun: () => void;
  scenario: Scenario | null;
  sources: Loadable<ScenarioSources>;
  reloadSources: () => void;
  rv: RunView;
  runLabel: string;
  runDot: string;
  running: boolean;
  clockText: string;

  replaying: boolean;
  speed: number;
  setSpeed: (n: number) => void;
  replay: () => void;
  stopReplay: () => void;

  sel: string;
  setSel: (id: string) => void;
  tab: DetailTab;
  setTab: (t: DetailTab) => void;
  group: GroupMode;
  setGroup: (g: GroupMode) => void;
  openImp: string | null;
  setOpenImp: (id: string | null) => void;
  focusNonce: number;
  openImpact: (id: string) => void;
  openSource: (sourceId: string, quote: string) => void;


  staged: Staged;
  setStaged: (s: Staged) => void;
  openPicker: () => void;
  flash: (text: string, ms?: number) => void;

  /**
   * The regulation the user chose in the law interface (set by the Act screen and by opening an
   * analysis); the sidebar card shows it there, and the Act header its comparison.
   */
  actContext: ActContext | null;
  setActContext: (c: ActContext | null) => void;

  /** The view preset in use: which screens show and where navigation lands. */
  role: Role;
  setRole: (id: RoleId) => void;
  can: (screen: ScreenId) => boolean;

  /** The user's role in the law (an actor id, or "public"): Act counts and Analysis filters follow it. */
  actor: string;
  setActor: (actor: string) => void;

  /** Open the Act screen at a comparison and article (from the Regulations screen). */
  openAct: (target: ActTarget) => void;
  /** The pending target; the Act screen applies it once and clears it. */
  actTarget: ActTarget | null;
  clearActTarget: () => void;
}

export interface ActContext {
  /** Header subtitle, e.g. "Regulation (EU) 2024/1689 → consolidated on 27 July 2026". */
  comparison: string;
  /** Sidebar card: regulation title and the version pair. */
  regulation: string;
  versions: string;
}

export const Ctx = createContext<ConsoleCtx | null>(null);

export function useConsole(): ConsoleCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useConsole outside <Ctx.Provider>');
  return c;
}
