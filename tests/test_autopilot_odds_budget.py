from scripts import autopilot as ap


def test_paid_odds_pull_stays_off():
    ok, why = ap.nfl_odds_pull_allowed([], ap.datetime.now(ap.ET))
    assert ok is False
    assert "Polymarket" in why


def test_run_model_only_passes_odds_flag_when_allowed(monkeypatch):
    seen = []

    class Done:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(ap.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or Done())
    ap.run_model(2026, 11)
    ap.run_model(2026, 11, with_odds=True)
    assert "--odds" not in seen[0] and "--odds" in seen[1]
