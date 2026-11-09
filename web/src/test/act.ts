// Act screen fixtures: two comparisons and a small whole-act comparison per pair.
import type { ActComparison, ArticleComparison, ComparisonRef, ObligationSummary } from '../types';

const v = (version_id: string, label: string, status: string) => ({ version_id, label, celex: version_id, date: '2024-06-13', status });
export const PROPOSAL = v('com2021_206', 'COM(2021) 206', 'proposal');
export const FINAL = v('reg2024_1689', 'Regulation (EU) 2024/1689', 'adopted');
export const CONSOLIDATED = v('reg2024_1689_c20260727', 'Regulation (EU) 2024/1689 as consolidated on 27 July 2026', 'consolidated');

/** The proposal pair maps onto the sample run's scenario, so its findings land on Art 57. */
export const COMPARISONS: ComparisonRef[] = [
  { regulation_id: 'ai_act', before: PROPOSAL, after: FINAL, scenario_ids: ['eval_sme_impacts'] },
  { regulation_id: 'ai_act', before: FINAL, after: CONSOLIDATED, scenario_ids: ['omnibus_2026'] },
];

export const ob = (over: Partial<ObligationSummary> = {}): ObligationSummary => ({ duties: 0, by_actor: {}, public_sector: 0, date_withheld: {}, obligation_ids: [], ...over });

export const art = (over: Partial<ArticleComparison>): ArticleComparison => ({
  provision_key: 'ai_act/art/1',
  kind: 'article',
  number: '1',
  heading: 'Subject matter',
  before_number: '1',
  change: 'unchanged',
  superseded_before: false,
  paragraphs: [],
  unplaced_before: null,
  unplaced_after: null,
  ...over,
});

const same = (label: string, text: string, o = ob()) => ({ label, change: 'unchanged' as const, before_text: text, after_text: text, before_obligations: o, after_obligations: o });

export const OMNIBUS: ActComparison = {
  regulation_id: 'ai_act',
  title: 'AI Act',
  before: FINAL,
  after: CONSOLIDATED,
  paragraph_basis: 'number',
  articles: [
    art({ provision_key: 'ai_act/art/1', paragraphs: [same('1', '1. The purpose of this Regulation.')] }),
    art({
      provision_key: 'ai_act/art/26',
      number: '26',
      before_number: '26',
      heading: 'Obligations of deployers of high-risk AI systems',
      paragraphs: [
        same('1', '1. Deployers shall take appropriate measures.', ob({ duties: 2, by_actor: { deployer: 2 }, date_withheld: { date_moved_2026: 2 } })),
        same('2', '2. Deployers shall assign human oversight.', ob({ duties: 1, by_actor: { deployer: 1 }, date_withheld: { date_moved_2026: 1 } })),
      ],
    }),
    art({
      provision_key: 'ai_act/art/27',
      number: '27',
      before_number: '27',
      heading: 'Fundamental rights impact assessment',
      change: 'modified',
      superseded_before: true,
      paragraphs: [
        { label: '1', change: 'unchanged', before_text: '1. Deployers shall perform an assessment.', after_text: '1. Deployers shall perform an assessment.', before_obligations: ob({ duties: 3, by_actor: { deployer: 3 }, date_withheld: { amended_2026: 3 } }), after_obligations: ob() },
        { label: '4', change: 'modified', before_text: '4. The AI Office shall develop a template.', after_text: '4. The AI Office shall develop a simplified template.', before_obligations: ob({ duties: 1, by_actor: { ai_office: 1 }, date_withheld: { amended_2026: 1 } }), after_obligations: ob() },
      ],
    }),
    art({
      provision_key: 'ai_act/art/4a',
      number: '4a',
      before_number: null,
      heading: 'Processing of special categories of personal data',
      change: 'added',
      paragraphs: [{ label: '1', change: 'added', before_text: null, after_text: '1. Providers may exceptionally process special categories of personal data.', before_obligations: null, after_obligations: ob() }],
    }),
    art({ provision_key: 'ai_act/annex/XIV', kind: 'annex', number: 'XIV', before_number: null, heading: 'New annex', change: 'added', paragraphs: [{ label: '', change: 'added', before_text: null, after_text: 'Information to be submitted.', before_obligations: null, after_obligations: ob() }] }),
  ],
};

export const PROPOSAL_TO_FINAL: ActComparison = {
  ...OMNIBUS,
  before: PROPOSAL,
  after: FINAL,
  articles: [
    art({
      provision_key: 'ai_act/innovation/regulatory_sandboxes',
      number: '57',
      before_number: '53',
      heading: 'AI regulatory sandboxes',
      change: 'modified',
      paragraphs: [{ label: '1', change: 'modified', before_text: '1. AI regulatory sandboxes may be established.', after_text: '1. Member States shall ensure that AI regulatory sandboxes are established.', before_obligations: ob(), after_obligations: ob({ duties: 1, by_actor: { member_state: 1 } }) }],
    }),
  ],
};
