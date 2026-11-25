// Dispatch logic for the Vercel Schedules backstop (api/schedules/dispatch.mjs).
//
// GitHub's scheduled triggers are best-effort and regularly drop or delay
// ticks, which shows up as staleness alarms. Vercel Schedules calls the
// private dispatch function on the same cadence, and it fires
// workflow_dispatch for the time-sensitive workflows. The GitHub crons stay
// as a second trigger; each workflow's concurrency group (cancel-in-progress
// false) queues an overlapping run instead of racing it.
//
// Two triggers for one workflow would open two data PRs touching the same
// files (open_automerge_data_pr.sh opens a new timestamped PR per run), so
// before dispatching the function asks GitHub whether that workflow already
// has a run for this slot (created at or after the slot's scheduled time,
// less SLOT_SLACK_MS) that is queued, in progress or succeeded, and skips
// if so. It cannot stop a GitHub cron run that starts after its own
// dispatch; the workflow concurrency group then runs the two back to back.
//
// Any dispatch failure (missing token, GitHub 401/403, any non-2xx) POSTs
// to ALARM_WEBHOOK_URL, the same channel feed_age.py pages, and then fails
// the run. An expired token therefore alerts at the next tick, not when
// the freshness alarm notices stale data hours later.
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
  'deploy-gate-sweep': 'deploy_gate.yml',
});

/**
 * workflow_dispatch inputs per schedule. The deploy gate's hourly sweep
 * (deploy everything served that the 60-min data throttle left behind)
 * runs from GitHub's cron, which fired 2 times in 24 hours on 2026-11-24.
 * Once automatic git deploys are off, that sweep is the only catch-up for
 * the last data merge of the night, so it rides the same backstop as the
 * odds feed.
 */
export const SCHEDULE_INPUTS = Object.freeze({
  'deploy-gate-sweep': Object.freeze({ sweep: 'true' }),
});

/**
 * Which run events cover a slot, per schedule (default: any event). The
 * deploy gate also runs on every push to main, and a push run that skipped
 * a data change under the 60-min throttle must not stand in for the sweep.
 */
export const SCHEDULE_COVER_EVENTS = Object.freeze({
  'deploy-gate-sweep': Object.freeze(['schedule', 'workflow_dispatch']),
});

/**
 * A run created this long before the slot's scheduled time still counts as
 * the same slot (clock skew between GitHub and Vercel). Far shorter than
 * any cadence, so the previous slot's run never suppresses this one.
 * Anchored to scheduledAt, not to "now", because Hobby delivers up to 15
 * minutes late and a GitHub cron run for this slot may already be done.
 */
export const SLOT_SLACK_MS = 2 * 60 * 1000;

const ACTIVE_STATUSES = new Set(['queued', 'in_progress', 'waiting', 'pending', 'requested']);

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

function githubHeaders(token) {
  return {
    Accept: 'application/vnd.github+json',
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
    'User-Agent': 'propstein-schedule-dispatch',
    'X-GitHub-Api-Version': '2022-11-28',
  };
}

function requireToken(token, workflow) {
  if (!token) {
    throw new DispatchError('GITHUB_DISPATCH_TOKEN is not set; cannot dispatch ' + workflow);
  }
}

/**
 * The newest run of `workflow` created inside the window that is still
 * active or finished successfully, or null. A failed or cancelled run does
 * not cover the slot, so the schedule dispatches a fresh one.
 */
export async function coveringRun(workflow, { token, since, repo = DEFAULT_REPO, events = null, fetchImpl = fetch }) {
  requireToken(token, workflow);
  const created = `>=${since.toISOString().replace(/\.\d{3}Z$/, 'Z')}`;
  const url = `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/runs`
    + `?per_page=20&created=${encodeURIComponent(created)}`;
  const response = await fetchImpl(url, { method: 'GET', headers: githubHeaders(token) });
  if (response.status !== 200) {
    const detail = (await response.text().catch(() => '')).slice(0, 300);
    throw new DispatchError(`GitHub run lookup for ${workflow} failed: HTTP ${response.status} ${detail}`);
  }
  const body = await response.json();
  const runs = Array.isArray(body.workflow_runs) ? body.workflow_runs : [];
  return runs.find((run) => (!events || events.includes(run.event))
    && (ACTIVE_STATUSES.has(run.status)
      || (run.status === 'completed' && run.conclusion === 'success'))) || null;
}

export async function dispatchWorkflow(workflow, { token, repo = DEFAULT_REPO, ref = DEFAULT_REF, inputs = null, fetchImpl = fetch }) {
  requireToken(token, workflow);
  const url = `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`;
  const response = await fetchImpl(url, {
    method: 'POST',
    headers: githubHeaders(token),
    body: JSON.stringify(inputs ? { ref, inputs } : { ref }),
  });
  if (response.status !== 204) {
    // Never echo the token; GitHub's error body does not contain it.
    const detail = (await response.text().catch(() => '')).slice(0, 300);
    throw new DispatchError(`GitHub dispatch of ${workflow} failed: HTTP ${response.status} ${detail}`);
  }
  return { workflow, ref, repo, inputs };
}

/**
 * Page ALARM_WEBHOOK_URL ({"content", "text"}: Discord or Slack, the shape
 * feed_age.py posts). Never throws: a broken webhook must not mask the
 * dispatch failure it reports. Neither the token nor the URL is logged.
 */
export async function sendAlarm(message, { env = process.env, fetchImpl = fetch, log = console } = {}) {
  const url = env.ALARM_WEBHOOK_URL;
  if (!url) {
    log.error('[schedule-dispatch] ALARM_WEBHOOK_URL is not set; alert is in the function log only');
    return false;
  }
  try {
    const response = await fetchImpl(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: message, text: message }),
    });
    if (response.status < 200 || response.status >= 300) {
      log.error(`[schedule-dispatch] alarm webhook returned HTTP ${response.status}`);
      return false;
    }
    return true;
  } catch (err) {
    log.error(`[schedule-dispatch] alarm webhook failed: ${err?.name || 'Error'}`);
    return false;
  }
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
  const token = env.GITHUB_DISPATCH_TOKEN;
  const repo = env.GITHUB_DISPATCH_REPO || DEFAULT_REPO;
  try {
    const since = new Date((Number.isFinite(scheduledAt.getTime()) ? scheduledAt : now).getTime() - SLOT_SLACK_MS);
    const events = SCHEDULE_COVER_EVENTS[metadata.name] || null;
    const existing = await coveringRun(workflow, { token, repo, fetchImpl, since, events });
    if (existing) {
      const result = {
        status: 'skipped_covered', schedule: metadata.name, workflow,
        run_id: existing.id, run_status: existing.status, run_event: existing.event,
      };
      log.log(`[schedule-dispatch] ${JSON.stringify(result)}`);
      return result;
    }
    const inputs = SCHEDULE_INPUTS[metadata.name] || null;
    await dispatchWorkflow(workflow, { token, repo, fetchImpl, inputs });
  } catch (err) {
    const message = `PropStein SCHEDULE DISPATCH FAILED: ${metadata.name} -> ${workflow}: ${err.message}`;
    log.error(`[schedule-dispatch] ${message}`);
    await sendAlarm(message, { env, fetchImpl, log });
    throw err;
  }
  const result = { status: 'dispatched', schedule: metadata.name, workflow, execution_id: metadata.executionId };
  log.log(`[schedule-dispatch] ${JSON.stringify(result)}`);
  return result;
}
