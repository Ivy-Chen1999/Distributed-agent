// The published evaluation of a system version (Overview), as counts with a range. Pure.
import type { EvalMetric, EvalReport } from '../types';

export interface EvalRow {
  name: string;
  /** "41 of 52" for counted metrics, "92%" for rates. */
  value: string;
  /** "about 65–88%" from the case-level bootstrap, or "" with fewer than two cases. */
  range: string;
  help: string;
}

const pct = (x: number): string => `${Math.round(x * 100)}%`;

function rangeText(m: EvalMetric): string {
  if (!m.range) return '';
  const [lo, hi] = m.range.map((x) => Math.round(x * 100));
  return lo === hi ? `about ${lo}%` : `about ${lo}–${hi}%`;
}

function counted(m: EvalMetric): string {
  if (m.found === undefined || m.total === undefined || m.total === 0) return '—';
  return `${m.found} of ${m.total}`;
}

/** Rows in a fixed order; rows without data are left out. */
export function evalRows(report: EvalReport): EvalRow[] {
  const s = report.summary;
  const rows: EvalRow[] = [];
  if (s.reference_impacts.total) {
    rows.push({ name: 'Reference impacts found', value: counted(s.reference_impacts), range: rangeText(s.reference_impacts), help: 'Impacts in the reference cases that the analysis found. It does not count impacts the analysis added that are not in the reference.' });
  }
  if (s.omissions_addressed.total) {
    rows.push({ name: 'Omissions addressed', value: counted(s.omissions_addressed), range: rangeText(s.omissions_addressed), help: 'Important gaps in the reference cases that the analysis raised.' });
  }
  if (s.grounding.value !== null) {
    rows.push({ name: 'Citation check', value: pct(s.grounding.value), range: rangeText(s.grounding), help: 'Share of quotes found word for word in their source.' });
  }
  if (s.precision.value !== null) {
    rows.push({ name: 'Matched to a reference', value: pct(s.precision.value), range: rangeText(s.precision), help: 'Share of the analysis’s impacts that match a reference impact.' });
  }
  return rows;
}

/** "8 train and 3 val reference cases, 2 runs each" and excluded runs, if any. */
export function evalBasis(report: EvalReport): string {
  const s = report.summary;
  const splits = Object.entries(s.splits).map(([split, n]) => `${n} ${split}`);
  const cases = `${splits.join(' and ')} reference case${s.cases === 1 ? '' : 's'}`;
  const reps = report.repetitions > 1 ? `, ${report.repetitions} runs each` : '';
  const excluded = s.excluded_runs ? `; ${s.excluded_runs} run${s.excluded_runs === 1 ? '' : 's'} without a verdict left out` : '';
  return `${cases}${reps}${excluded}`;
}
