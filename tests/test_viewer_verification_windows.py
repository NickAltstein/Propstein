"""Keep the viewer's verification windows equal to the Python contract."""
import json
from pathlib import Path

from src.contracts.verification import VerificationConfig, VERIFICATION_CONTRACTS_VERSION


def test_viewer_windows_match_verification_contract():
    path = Path(__file__).resolve().parents[1] / "viewer/verification_windows.json"
    viewer = json.loads(path.read_text(encoding="utf-8"))
    config = VerificationConfig()
    assert viewer == {
        "contract": VERIFICATION_CONTRACTS_VERSION,
        "staleness_pregame_minutes": config.staleness_pregame_minutes,
        "staleness_near_kickoff_minutes": config.staleness_near_kickoff_minutes,
        "staleness_in_game_minutes": config.staleness_in_game_minutes,
        "near_kickoff_window_minutes": config.near_kickoff_window_minutes,
    }
