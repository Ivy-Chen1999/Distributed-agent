// Plain-language explanations for failure kinds, shown next to the short codes (`timeout`,
// `rate_limit`, ...) so a reader knows what happened and what to try. Pure and self-contained.

export interface FailureHelp {
  /** What happened, in one sentence. */
  what: string;
  /** What to try next, in one sentence. */
  next: string;
}

const HELP: Record<string, FailureHelp> = {
  timeout: {
    what: 'The model took too long to answer and was stopped.',
    next: 'Run again. If it keeps happening, the model provider may be slow or overloaded.',
  },
  rate_limit: {
    what: 'The model provider received too many requests and refused this one.',
    next: 'Wait a minute, then run again.',
  },
  auth: {
    what: 'The model provider rejected the API key or login.',
    next: 'Check the API key in the backend settings (or run claude /login for the local Claude Code backend), restart the backend, then run again.',
  },
  schema_invalid: {
    what: 'The model answered, but not in the required format, even after retries.',
    next: 'Run again. If it keeps happening, the prompt or model for this role may need changing.',
  },
  process_error: {
    what: 'The model call crashed before it returned an answer.',
    next: 'Run again, then check the backend terminal for the error message.',
  },
  no_data_in_scope: {
    what: "This expert's part of the regulation has no provisions in this run, so it was skipped. This is not an error.",
    next: 'Nothing to do.',
  },
  pipeline_failed: {
    what: 'The run stopped because a required step failed.',
    next: 'Open the event log to see which step failed, then run again.',
  },
  orphaned: {
    what: 'The backend restarted while this run was in progress, so the run was marked failed.',
    next: 'Start the run again.',
  },
  cancelled: {
    what: 'The run was stopped before it finished.',
    next: 'Start it again if you still need it.',
  },
};

/** Explanation for a failure kind; an unknown kind gets a generic one that still names the code. */
export function failureHelp(kind: string | null | undefined): FailureHelp {
  const known = kind ? HELP[kind] : undefined;
  if (known) return known;
  return {
    what: kind ? `Something went wrong (code: ${kind.replace(/_/g, ' ')}).` : 'Something went wrong.',
    next: 'Run again, and check the event log for details.',
  };
}

/** Shown on a finding whose quote the citation check could not find in its source. */
export const UNRESOLVED_QUOTE_HELP =
  'The citation check could not find this finding’s quote word for word in the source. Treat the finding as unverified until you have checked the source yourself.';
