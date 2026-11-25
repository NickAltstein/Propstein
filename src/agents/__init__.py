"""
PropStein Live Agentic Sniper Terminal (NFL + NBA + Polymarket).

Exports the 6-agent topology, deterministic `price_prop` function,
`StaleKillSwitch`, `DurableRedisStreamBus`, `TruthStore`, and
`SniperTerminalEngine` (LangGraph `StateGraph(AgentState)`).

Exports load lazily on first access, so light jobs (such as the
availability poll, which imports only `src.agents.injury_consumer`) do not
pull in LangGraph and the rest of the agent stack.
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AgentState": "src.agents.schemas",
    "DurableRedisStreamBus": "src.agents.persistence",
    "InjuryBeneficiary": "src.agents.schemas",
    "InjuryEventAgent": "src.agents.injury_agent",
    "InjuryEventPayload": "src.agents.schemas",
    "LineupAndSubstitutionAgent": "src.agents.lineup_agent",
    "LineupEventPayload": "src.agents.schemas",
    "MODEL_VERSION": "src.agents.schemas",
    "MarketIngestAgent": "src.agents.market_agent",
    "MarketMovePayload": "src.agents.schemas",
    "PolymarketArbAgent": "src.agents.polymarket_arb_agent",
    "PolymarketPropOpportunity": "src.agents.schemas",
    "PolymarketSpecialistAgent": "src.agents.polymarket_agent",
    "ProjectionRecalcAgent": "src.agents.recalc_agent",
    "PropProjection": "src.agents.schemas",
    "SniperAgent": "src.agents.sniper_agent",
    "SniperAlert": "src.agents.schemas",
    "SniperGateConfig": "src.agents.schemas",
    "SniperTerminalEngine": "src.agents.graph",
    "StaleKillSwitch": "src.agents.stale_kill_switch",
    "StaleThresholds": "src.agents.schemas",
    "StructuredAgentLog": "src.agents.schemas",
    "TruthStore": "src.agents.persistence",
    "compute_clob_liquidity_score": "src.agents.polymarket_agent",
    "compute_cross_venue_arb": "src.agents.polymarket_arb_agent",
    "compute_dedup_hash": "src.agents.schemas",
    "compute_fee_adjusted_yes_prices": "src.agents.polymarket_arb_agent",
    "compute_source_hash": "src.agents.schemas",
    "compute_top3_orderbook_vwap": "src.agents.polymarket_arb_agent",
    "narrate_locked_alert": "src.agents.sniper_agent",
    "price_prop": "src.agents.recalc_agent",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = [
    "MODEL_VERSION",
    "AgentState",
    "DurableRedisStreamBus",
    "InjuryBeneficiary",
    "InjuryEventAgent",
    "InjuryEventPayload",
    "LineupAndSubstitutionAgent",
    "LineupEventPayload",
    "MarketIngestAgent",
    "MarketMovePayload",
    "PolymarketPropOpportunity",
    "PolymarketSpecialistAgent",
    "ProjectionRecalcAgent",
    "PropProjection",
    "SniperAgent",
    "SniperAlert",
    "SniperGateConfig",
    "SniperTerminalEngine",
    "StaleKillSwitch",
    "StaleThresholds",
    "StructuredAgentLog",
    "TruthStore",
    "compute_clob_liquidity_score",
    "compute_dedup_hash",
    "compute_source_hash",
    "narrate_locked_alert",
    "price_prop",
]
