#!/usr/bin/env python3
"""Decide on every push to main whether to deploy production, then deploy
through a Vercel deploy hook and confirm the deployment really appeared.

Why: every push to main used to create a Vercel deployment. Data-bot merges
alone produced about 40 a day, most cancelled by scripts/vercel_ignore.sh
but still counted against the Hobby limit of 100 deployments a day. This
gate runs on each push (not on a cron, which is the unreliable part) and
compares main against the commit of the last READY production deployment:

  code   a served non-data file changed (UI, config)  -> deploy now
  data   only served data files changed               -> deploy only if the
         last READY production deploy is older than --min-data-interval
         (60 min) and no deployment is already in flight; otherwise skip.
         The next data merge catches up, because the diff is always taken
         against the last deployed commit, not the previous push.
  none   nothing served changed                       -> skip

The hourly sweep (--sweep, from the workflow's schedule) drops the 60-min
throttle: it deploys whenever anything served differs from the last
production deploy and nothing is in flight. That closes the gap where the
last data merge of the night lands inside the hour and nothing follows it.
A missed sweep costs nothing; the next push or sweep catches up.

While vercel.json still has automatic git deployments on, the gate only
reports its decision (Vercel is already deploying every push; a hook call
would deploy twice). A manual workflow_dispatch with force=true still calls
the hook, which is how the hook is tested before automatic deploys are
turned off.

State comes from Vercel's deployments API (last READY production
deployment: time and commit), never from memory or a state file.

After calling the hook it waits for the new production deployment's
final state, because "a deployment was created" is not "the site changed":

  READY (and promoted)            success
  CANCELED by the Ignored Build   success, logged as "no change", only when
    Step, on the exact commit     production is already on that commit
    production already serves
  anything else: CANCELED for     fail: page ALARM_WEBHOOK_URL and exit 1
    another reason, ERROR, built
    but not promoted, or still
    not final at --ready-timeout

If the hook call fails, or it is accepted but no deployment appears within
--verify-timeout, the hook is tried once more. If that fails too, the gate
creates the deployment directly through the Vercel API (POST
/v13/deployments from the GitHub commit), pages that the hook is broken,
and still waits for READY. Once automatic git deploys are off this gate is
the only route to production, so it never reports success on less than a
live deploy. The deploy hook URL can trigger a production deploy: it is
read from the environment, used only as a request target, and never printed.

Env:
  VERCEL_DEPLOY_HOOK_URL  secret; the main-branch deploy hook
  VERCEL_API_TOKEN        secret; reads deployments, and creates one when
                          the hook fails twice
  VERCEL_PROJECT_ID, VERCEL_TEAM_ID, VERCEL_PROJECT_NAME
  GITHUB_REPOSITORY_ID    set by Actions; needed for the API fallback
  ALARM_WEBHOOK_URL       secret; optional {"content","text"} pager
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch

API = "https://api.vercel.com"
CODE, DATA, NONE = "code", "data", "none"

#: Same deployed paths as scripts/vercel_ignore.sh (the Ignored Build Step).
SERVED_PREFIXES = ("viewer/", "public/", "api/", "src/")
SERVED_FILES = ("requirements.txt", "package.json", "package-lock.json", "vercel.json", ".vercelignore")
#: Served but never worth a deploy on their own (vercel_ignore.sh excludes them).
NOT_SERVED = ("viewer/news_watcher_state.json", "public/news_watcher_state.json")
#: Served files that are data: refreshed by bots, safe to batch hourly.
DATA_PATTERNS = ("viewer/*.json", "viewer/*.csv", "public/*.json", "public/*.csv")
#: Data-looking files that change how the site behaves: deploy at once.
CODE_JSON = ("viewer/manifest.json",)

IN_FLIGHT = {"BUILDING", "INITIALIZING", "QUEUED"}
FINAL = {"READY", "ERROR", "CANCELED"}
#: Vercel's message when scripts/vercel_ignore.sh exits 0.
IGNORED_BUILD = "Ignored Build Step"


def classify_path(path: str) -> str:
    if path in NOT_SERVED:
        return NONE
    served = path.startswith(SERVED_PREFIXES) or path in SERVED_FILES
    if not served:
        return NONE
    if path in CODE_JSON:
        return CODE
    if any(fnmatch(path, pat) for pat in DATA_PATTERNS):
        return DATA
    return CODE


def classify(paths: list[str]) -> str:
    kinds = {classify_path(p) for p in paths if p}
    if CODE in kinds:
        return CODE
    if DATA in kinds:
        return DATA
    return NONE


def auto_deploys_enabled(vercel_json: str = "vercel.json") -> bool:
    """True unless vercel.json sets git.deploymentEnabled to false for main."""
    try:
        cfg = json.loads(open(vercel_json, encoding="utf-8").read())
    except (OSError, ValueError):
        return True
    rule = (cfg.get("git") or {}).get("deploymentEnabled", True)
    if isinstance(rule, bool):
        return rule
    if isinstance(rule, dict):
        return any(on for pat, on in rule.items() if fnmatch("main", pat))
    return True


def decide(kind: str, *, last_ready_at: datetime | None, in_flight: bool, now: datetime,
           min_data_interval: timedelta, force: bool = False, sweep: bool = False) -> tuple[bool, str]:
    """(deploy?, reason). Pure: no I/O."""
    if force:
        return True, "forced by workflow_dispatch"
    if kind == CODE:
        return True, "served code/config changed since the last production deploy"
    if kind == NONE:
        return False, "nothing served changed since the last production deploy"
    if in_flight:
        return False, "data-only change and a production deployment is already in flight"
    if sweep:
        return True, "hourly sweep: served data changed since the last production deploy"
    if last_ready_at is None:
        return True, "data changed and no READY production deployment is on record"
    age = now - last_ready_at
    if age >= min_data_interval:
        return True, f"data changed and the last production deploy is {age.total_seconds() / 60:.0f} min old"
    return False, (f"data-only change; last production deploy is {age.total_seconds() / 60:.0f} min old "
                   f"(< {min_data_interval.total_seconds() / 60:.0f} min), the next data merge catches up")


def dep_state(dep: dict) -> str | None:
    return dep.get("readyState") or dep.get("state")


def judge_final(dep: dict | None, *, production_sha: str | None) -> tuple[bool, str]:
    """(ok?, message) for a production deployment's final state. Pure."""
    if dep is None:
        return False, "no final state"
    state = dep_state(dep)
    sha = deployment_sha(dep)
    short = (sha or "?")[:9]
    if state == "READY":
        sub = dep.get("readySubstate")
        if sub and sub != "PROMOTED":
            return False, f"deployment {short} built but is {sub}, not live"
        return True, f"deployment {short} is READY and live"
    if state == "CANCELED":
        why = dep.get("errorMessage") or "no reason given"
        if IGNORED_BUILD in why and sha and sha == production_sha:
            return True, f"no change: production already serves {short}, so Vercel skipped the build"
        return False, f"deployment {short} was CANCELED: {why}"
    if state == "ERROR":
        return False, f"deployment {short} failed to build: {dep.get('errorMessage') or 'no reason given'}"
    return False, f"deployment {short} still {state or 'unknown'} at the timeout"


# ── I/O ──────────────────────────────────────────────────────────────────

class GateError(RuntimeError):
    pass


def _ms(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def list_production(token: str, project: str, team: str | None, *, state: str | None = None,
                    limit: int = 10, opener=urllib.request.urlopen) -> list[dict]:
    params = {"projectId": project, "target": "production", "limit": str(limit)}
    if team:
        params["teamId"] = team
    if state:
        params["state"] = state
    req = urllib.request.Request(f"{API}/v6/deployments?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with opener(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")).get("deployments") or []
    except urllib.error.HTTPError as exc:
        raise GateError(f"Vercel deployments API returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise GateError(f"Vercel deployments API unreachable ({type(exc).__name__})") from None


def deployment_sha(dep: dict) -> str | None:
    meta = dep.get("meta") or {}
    return meta.get("githubCommitSha") or None


def get_deployment(token: str, dep_id: str, team: str | None,
                   opener=urllib.request.urlopen) -> dict:
    params = {"teamId": team} if team else {}
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(f"{API}/v13/deployments/{urllib.parse.quote(dep_id)}{query}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with opener(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GateError(f"Vercel deployment API returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise GateError(f"Vercel deployment API unreachable ({type(exc).__name__})") from None


def create_deployment(token: str, *, project: str, name: str, team: str | None, repo_id: str,
                      sha: str, opener=urllib.request.urlopen) -> dict:
    """Production deployment of `sha` on main, straight from the GitHub repo."""
    params = {"teamId": team} if team else {}
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    body = json.dumps({"name": name, "project": project, "target": "production",
                       "gitSource": {"type": "github", "repoId": repo_id, "ref": "main", "sha": sha}})
    req = urllib.request.Request(f"{API}/v13/deployments{query}", data=body.encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    try:
        with opener(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GateError(f"Vercel create-deployment API returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise GateError(f"Vercel create-deployment API unreachable ({type(exc).__name__})") from None


def call_hook(url: str, opener=urllib.request.urlopen) -> dict:
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with opener(req, timeout=20) as resp:
            if resp.status // 100 != 2:
                raise GateError(f"deploy hook returned HTTP {resp.status}")
            try:
                return json.loads(resp.read().decode("utf-8") or "{}")
            except ValueError:
                return {}
    except urllib.error.HTTPError as exc:
        hint = " (rate limited)" if exc.code == 429 else ""
        raise GateError(f"deploy hook returned HTTP {exc.code}{hint}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        # Never include the URL: it is the secret.
        raise GateError(f"deploy hook unreachable ({type(exc).__name__})") from None


def wait_for_new_deployment(list_fn, *, after: datetime, timeout_s: float, interval_s: float,
                            sleep=None, clock=None) -> dict | None:
    """First production deployment created at/after `after`, or None."""
    sleep = sleep or time.sleep  # looked up per call so tests can patch time.sleep
    clock = clock or time.monotonic
    deadline = clock() + timeout_s
    while True:
        for dep in list_fn():
            created = _ms(dep.get("createdAt") or dep.get("created"))
            if created and created >= after - timedelta(seconds=5):
                return dep
        if clock() >= deadline:
            return None
        sleep(interval_s)


def wait_for_final_state(get_fn, *, timeout_s: float, interval_s: float,
                         sleep=None, clock=None) -> dict | None:
    """Poll one deployment until READY, ERROR or CANCELED. The last state
    seen if the timeout passes first (judge_final then fails it)."""
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + timeout_s
    dep = None
    while True:
        dep = get_fn()
        if dep_state(dep) in FINAL or clock() >= deadline:
            return dep
        sleep(interval_s)


def send_alarm(message: str, url: str | None, opener=urllib.request.urlopen) -> bool:
    if not url:
        print("ALARM_WEBHOOK_URL is not set; the failed job is the alarm", file=sys.stderr)
        return False
    body = json.dumps({"content": message, "text": message}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with opener(req, timeout=15) as resp:
            resp.read()
        return True
    except Exception as exc:  # a broken pager must not hide the failure
        print(f"alarm webhook failed: {type(exc).__name__}", file=sys.stderr)
        return False


def changed_paths(base: str | None, head: str = "HEAD") -> list[str] | None:
    """Files changed between the last deployed commit and head, or None if
    the base commit is unknown or not in this clone (caller deploys)."""
    if not base:
        return None
    ok = subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"], capture_output=True)
    if ok.returncode != 0:
        return None
    out = subprocess.run(["git", "diff", "--name-only", base, head], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return [line for line in out.stdout.splitlines() if line.strip()]


def summary(line: str) -> None:
    print(line)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def run(args, env=os.environ) -> int:
    token = env.get("VERCEL_API_TOKEN", "").strip()
    project = env.get("VERCEL_PROJECT_ID", "").strip()
    team = env.get("VERCEL_TEAM_ID", "").strip() or None
    hook = env.get("VERCEL_DEPLOY_HOOK_URL", "").strip()
    alarm = env.get("ALARM_WEBHOOK_URL", "").strip() or None
    now = datetime.now(timezone.utc)

    def fail(msg: str) -> int:
        full = f"PropStein PRODUCTION DEPLOY FAILED: {msg}"
        summary(full)
        send_alarm(full, alarm)
        return 1

    if not token or not project:
        return fail("VERCEL_API_TOKEN or VERCEL_PROJECT_ID is not set")
    try:
        ready = list_production(token, project, team, state="READY", limit=1)
        recent = list_production(token, project, team, limit=5)
    except GateError as exc:
        return fail(str(exc))
    last = ready[0] if ready else None
    last_sha = deployment_sha(last) if last else None
    last_at = _ms((last or {}).get("ready") or (last or {}).get("createdAt"))
    in_flight = any((d.get("state") or d.get("readyState")) in IN_FLIGHT for d in recent)

    paths = changed_paths(last_sha, args.head)
    kind = CODE if paths is None else classify(paths)
    should, reason = decide(kind, last_ready_at=last_at, in_flight=in_flight, now=now,
                            min_data_interval=timedelta(minutes=args.min_data_interval),
                            force=args.force, sweep=args.sweep)
    summary(f"deploy gate: last production {last_sha[:9] if last_sha else 'unknown'} "
            f"at {last_at.isoformat() if last_at else 'unknown'}; change: {kind}"
            f"{'' if paths is None else f' ({len(paths)} file(s))'}; "
            f"{'DEPLOY' if should else 'SKIP'}: {reason}")
    if not should or args.dry_run:
        return 0
    if auto_deploys_enabled(args.vercel_json) and not args.force:
        summary("deploy gate: report only; vercel.json still deploys main automatically, "
                "so no hook call (run workflow_dispatch with force to test the hook)")
        return 0
    if not hook:
        return fail("VERCEL_DEPLOY_HOOK_URL is not set; a deploy was needed and none was triggered")

    def list_recent():
        return list_production(token, project, team, limit=5)

    dep, problems = None, []
    for attempt in (1, 2):
        called_at = datetime.now(timezone.utc)
        try:
            call_hook(hook)
            dep = wait_for_new_deployment(list_recent, after=called_at,
                                          timeout_s=args.verify_timeout, interval_s=15)
        except GateError as exc:
            problems.append(f"hook attempt {attempt}: {exc}")
            continue
        if dep is not None:
            break
        problems.append(f"hook attempt {attempt}: accepted but no production deployment appeared "
                        f"within {args.verify_timeout:.0f}s")
    for p in problems:
        summary(f"deploy gate: {p}")

    if dep is None:
        repo_id = env.get("GITHUB_REPOSITORY_ID", "").strip()
        name = env.get("VERCEL_PROJECT_NAME", "").strip()
        sha = head_sha(args.head)
        if not (repo_id and name and sha):
            return fail("; ".join(problems) + "; no API fallback (GITHUB_REPOSITORY_ID, "
                        "VERCEL_PROJECT_NAME or the head commit is missing)")
        try:
            dep = create_deployment(token, project=project, name=name, team=team,
                                    repo_id=repo_id, sha=sha)
        except GateError as exc:
            return fail("; ".join(problems) + f"; API fallback failed: {exc}")
        send_alarm("PropStein deploy hook FAILED twice (" + "; ".join(problems) +
                   f"). Deployed {sha[:9]} through the Vercel API instead; fix or replace the hook.",
                   alarm)
        summary(f"deploy gate: hook failed twice; created deployment through the API for {sha[:9]}")

    dep_id = dep.get("uid") or dep.get("id")
    if not dep_id:
        return fail("deployment found but it has no id to follow")
    try:
        final = wait_for_final_state(lambda: get_deployment(token, dep_id, team),
                                     timeout_s=args.ready_timeout, interval_s=15)
    except GateError as exc:
        return fail(f"deployment {dep_id} created but its state could not be read: {exc}")
    ok, message = judge_final(final, production_sha=last_sha)
    if not ok:
        return fail(message)
    summary(f"deploy gate: {message}")
    return 0


def head_sha(head: str = "HEAD") -> str | None:
    out = subprocess.run(["git", "rev-parse", "--verify", f"{head}^{{commit}}"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--min-data-interval", type=float, default=60.0, help="minutes")
    ap.add_argument("--verify-timeout", type=float, default=180.0,
                    help="seconds to wait for a deployment to appear after each hook call")
    ap.add_argument("--ready-timeout", type=float, default=600.0,
                    help="seconds to wait for that deployment to reach READY, ERROR or CANCELED")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sweep", action="store_true",
                    help="scheduled catch-up: deploy any served change, ignoring the data throttle")
    ap.add_argument("--dry-run", action="store_true", help="decide and report, never call the hook")
    ap.add_argument("--vercel-json", default="vercel.json")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
