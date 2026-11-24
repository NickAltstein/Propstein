"""Alert steps must never print the webhook's response into Actions logs.

The repo is public, so its Actions logs are public. ALARM_WEBHOOK_URL is
masked, but curl -sS prints the response body, and ntfy's response names
the topic; anyone who knows an ntfy topic can read or post to it. Every
curl that posts to the webhook discards the body with -o /dev/null.
"""
import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _alarm_curls():
    for wf in sorted(WORKFLOWS.glob("*.y*ml")):
        for n, line in enumerate(wf.read_text().splitlines(), 1):
            if "curl" in line and "ALARM_WEBHOOK_URL" in line:
                yield wf.name, n, line.strip()


def test_there_are_alarm_curls_to_check():
    assert len(list(_alarm_curls())) >= 14


def test_no_alarm_curl_prints_the_response_body():
    for name, n, line in _alarm_curls():
        assert re.search(r"(?:^|\s)(?:-o|--output)\s+/dev/null(?:\s|$)", line), (
            f"{name}:{n} posts to ALARM_WEBHOOK_URL without -o /dev/null: {line}")


def test_no_alarm_curl_is_verbose():
    for name, n, line in _alarm_curls():
        assert not re.search(r"(?:^|\s)(?:-v|--verbose|--trace\S*)(?:\s|$)", line), f"{name}:{n}"
