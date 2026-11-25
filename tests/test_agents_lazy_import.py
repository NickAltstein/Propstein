"""The availability poll installs only pandas and pyarrow, so importing the
injury consumer must not drag in LangGraph or the rest of the agent stack."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_injury_consumer_import_does_not_load_agent_stack() -> None:
    code = (
        "import sys, src.agents.injury_consumer, src.availability_producer; "
        "heavy = [m for m in ('langgraph', 'src.agents.graph') if m in sys.modules]; "
        "assert not heavy, heavy"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


def test_package_exports_still_resolve() -> None:
    import src.agents as agents

    for name in agents.__all__:
        assert getattr(agents, name) is not None
