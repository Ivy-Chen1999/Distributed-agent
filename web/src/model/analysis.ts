// A run shown as a plain analysis (the law interface): what is missing from it, in words, instead
// of pipeline status, and which impacts concern the reader's role in the law. Pure.
import { ag } from '../design';
import type { ImpactDossier, RunStatus } from '../types';

/** Names in a readable list: "A", "A and B", "A, B and C". */
const listText = (names: string[]): string => (names.length < 2 ? (names[0] ?? '') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`);

/**
 * Why an analysis is incomplete, or null when it is complete. A degraded run either lost one or
 * more expert views (`failed_experts`) or its synthesis, in which case findings are unmerged.
 */
export function incompleteNote(status: RunStatus, d: ImpactDossier | null | undefined): string | null {
  const failed = [...new Set((d?.failed_experts ?? []).map((f) => ag(f.agent).n.toLowerCase()))];
  if (failed.length) {
    const views = `${listText(failed)} ${failed.length === 1 ? 'view is' : 'views are'}`;
    return `This analysis is incomplete: the ${views} missing, because ${failed.length === 1 ? 'that expert' : 'those experts'} did not finish. The other findings are unaffected.`;
  }
  if (status === 'degraded') return 'This analysis is incomplete: the final synthesis did not finish, so findings are listed as they were posted, without being merged.';
  return null;
}

/**
 * Wording that names an actor in a finding's free-text `affected_actor`. Findings have no actor
 * id, so this is a match on wording, labelled as such in the UI, never a silent filter.
 */
const ACTOR_WORDING: Record<string, RegExp> = {
  deployer: /\b(deployers?|users?)\b/i,
  provider: /\bproviders?\b/i,
  gpai_provider: /general[- ]purpose|\bgpai\b/i,
  importer: /\bimporters?\b/i,
  distributor: /\bdistributors?\b/i,
  authorised_representative: /authori[sz]ed representatives?/i,
  product_manufacturer: /\bmanufacturers?\b/i,
  operator: /\boperators?\b/i,
  public: /\bpublic (bod(y|ies)|authorit(y|ies)|sector|administrations?)\b|\bmunicipal|local government/i,
};

const escapeRx = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** An actor id as words, singular or plural: notified_body -> "notified body" or "notified bodies". */
function actorWords(actor: string): RegExp {
  const words = actor.split('_').map(escapeRx);
  const last = words.pop() ?? '';
  const plural = last.endsWith('y') ? `${last.slice(0, -1)}(y|ies)` : `${last}s?`;
  return new RegExp(`\\b${[...words, plural].join(' ')}\\b`, 'i');
}

export function mentionsActor(affectedActor: string, actor: string): boolean {
  return (ACTOR_WORDING[actor] ?? actorWords(actor)).test(affectedActor);
}

/** Impacts with at least one finding whose affected actor names the chosen role. */
export function impactsMentioning(d: ImpactDossier, actor: string): Set<string> {
  return new Set(d.impacts.filter((im) => im.findings.some((f) => mentionsActor(f.affected_actor, actor))).map((im) => im.impact_id));
}
