// Regulations screen fixture: the API lists corpus acts only (today the AI Act).
import type { RegulationEntry } from '../types';
import { COMPARISONS, CONSOLIDATED, FINAL, PROPOSAL } from './act';

export const AI_ACT: RegulationEntry = {
  regulation_id: 'ai_act',
  title: 'Artificial Intelligence Act',
  versions: [
    { ...PROPOSAL, celex: '52021PC0206' },
    { ...FINAL, celex: '32024R1689' },
    { ...CONSOLIDATED, celex: '02024R1689-20260727' },
  ],
  comparisons: [
    { ...COMPARISONS[0], before: { ...PROPOSAL, celex: '52021PC0206' }, after: { ...FINAL, celex: '32024R1689' } },
    { ...COMPARISONS[1], before: { ...FINAL, celex: '32024R1689' }, after: { ...CONSOLIDATED, celex: '02024R1689-20260727' } },
  ],
  scenarios: [
    { scenario_id: 'demo_penalties_amended', before_version: 'com2021_206', after_version: 'reg2024_1689', description: 'Provider obligations, SME measures and penalties.' },
    { scenario_id: 'eval_sme_impacts', before_version: null, after_version: 'com2021_206', description: 'Sandboxes, SME measures and penalties in the proposal.' },
    { scenario_id: 'omnibus_2026', before_version: 'reg2024_1689', after_version: 'reg2024_1689_c20260727', description: 'The Digital Omnibus amendments.' },
  ],
};

export const REGULATIONS: RegulationEntry[] = [AI_ACT];
