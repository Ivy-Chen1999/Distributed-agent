// Progress of a running analysis in plain words, for the law interface's Analysis screen (which
// hides the pipeline machinery). Pure, so it can be tested without the UI.
import type { NodeStatus } from '../design';
import { statusAt, type Trace } from './trace';

export type StepState = 'done' | 'active' | 'waiting' | 'failed';

export interface ProgressStep {
  label: string;
  state: StepState;
  /** For the experts step: how many have finished. */
  detail?: string;
}

const stateOf = (statuses: NodeStatus[]): StepState => {
  if (statuses.some((s) => s === 'running')) return 'active';
  if (statuses.every((s) => s === 'queued')) return 'waiting';
  if (statuses.some((s) => s === 'queued')) return 'active';
  if (statuses.every((s) => s === 'failed')) return 'failed';
  return 'done';
};

/** The five steps of an analysis and where the run is at `clock` (seconds from its start). */
export function analysisSteps(tr: Trace, clock: number): ProgressStep[] {
  const at = (id: string) => statusAt(tr, id, clock);
  const experts = tr.experts.map(at);
  const finished = experts.filter((s) => s === 'done').length;
  const failed = experts.filter((s) => s === 'failed').length;
  const started = experts.some((s) => s !== 'queued');
  return [
    { label: 'Reading the changes', state: stateOf([at('planner'), at('router')]) },
    {
      label: 'Experts reading the law',
      state: stateOf(experts),
      detail: started ? `${finished} of ${experts.length} finished${failed ? `, ${failed} failed` : ''}` : `${experts.length} experts`,
    },
    { label: 'Checking every quote against the source', state: stateOf([at('citation')]) },
    { label: 'Combining the findings', state: stateOf([at('synthesis')]) },
    { label: 'Writing the analysis', state: stateOf([at('dossier')]) },
  ];
}
