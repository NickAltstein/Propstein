// Dispatch logic for the Vercel Schedules backstop (api/schedules/dispatch.mjs).
//
// GitHub's scheduled triggers are best-effort and regularly drop or delay
// ticks, which shows up as staleness alarms. Vercel Schedules calls the
// private dispatch function on the same cadence, and it fires
// workflow_dispatch for the time-sensitive workflows. The GitHub crons stay
// as a second trigger; each workflow's concurrency group (cancel-in-progress
// false) queues an overlapping run instead of racing it.
//
// The dispatch function has a schedule/v1beta trigger, so it has no public
// URL: only Vercel's scheduler can invoke it. The GitHub token it uses is
// GITHUB_DISPATCH_TOKEN, a fine-grained PAT for this repository with
// Actions: write and nothing else, kept separate from DATA_BOT_TOKEN.
//
// Files under api/ that start with "_" are not deployed as functions.

/** Schedule name (vercel.json "schedules" key) -> workflow file to dispatch. */
export const SCHEDULE_WORKFLOWS = Object.freeze({
  'odds-freshness': 'odds_freshness.yml',
  'game-context-rollover': 'game_context_ingest.yml',
  'game-context-daily': 'game_context_ingest.yml',
  'close-quality-capture': 'close_quality_capture.yml',
});

export const DEFAULT_REPO = 'NickAltstein/Propstein';
export const DEFAULT_REF = 'main';

/**
 * Runs older than this are skipped. After an outage Vercel delivers every
 * missed occurrence; dispatching a backlog of stale odds or close-capture
 * runs at once only queues duplicate work. 15 minutes of Hobby jitter plus
 * slack.
 */
export const MAX_LATENESS_MS = 25 * 60 * 1000;

export class DispatchError extends Error {}

export async function dispatchWorkflow(workflow, { token, repo = DEFAULT_REPO, ref = DEFAULT_REF, fetchImpl = fetch }) {
  if (!token) {
    throw new DispatchError('GITHUB_DISPATCH_TOKEN is not set; cannot dispatch ' + workflow);
  }
  const url = `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`;
  const response = await fetchImpl(url, {
    method: 'POST',
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      'User-Agent': 'propstein-schedule-dispatch',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    body: JSON.stringify({ ref }),
  });
  if (response.status !== 204) {
    // Never echo the token; GitHub's error body does not contain it.
    const detail = (await response.text().catch(() => '')).slice(0, 300);
    throw new DispatchError(`GitHub dispatch of ${workflow} failed: HTTP ${response.status} ${detail}`);
  }
  return { workflow, ref, repo };
}

/**
 * Handle one schedule occurrence. Returns a status object for logging;
 * throws (-> HTTP 500 in Vercel's execution history) when a dispatch fails,
 * so a broken token or a renamed workflow is visible rather than silent.
 */
export async function runSchedule(metadata, { env = process.env, now = new Date(), fetchImpl = fetch, log = console } = {}) {
  const workflow = SCHEDULE_WORKFLOWS[metadata.name];
  if (!workflow) {
    throw new DispatchError(`unknown schedule ${JSON.stringify(metadata.name)}; no workflow mapped`);
  }
  const scheduledAt = new Date(metadata.scheduledAt);
  const lateMs = now.getTime() - scheduledAt.getTime();
  if (Number.isFinite(lateMs) && lateMs > MAX_LATENESS_MS) {
    const result = { status: 'skipped_stale', schedule: metadata.name, workflow, late_minutes: Math.round(lateMs / 60000) };
    log.warn(`[schedule-dispatch] ${JSON.stringify(result)}`);
    return result;
  }
  await dispatchWorkflow(workflow, {
    token: env.GITHUB_DISPATCH_TOKEN,
    repo: env.GITHUB_DISPATCH_REPO || DEFAULT_REPO,
    fetchImpl,
  });
  const result = { status: 'dispatched', schedule: metadata.name, workflow, execution_id: metadata.executionId };
  log.log(`[schedule-dispatch] ${JSON.stringify(result)}`);
  return result;
}
