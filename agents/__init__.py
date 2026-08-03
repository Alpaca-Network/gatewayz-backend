"""The Gatewayz GTM agent stack.

Six agents that run the go-to-market motion, all sending their inference through
Gatewayz itself. The dogfooding is deliberate: the monthly usage report
(``agents.dogfood_report``) is both a product-marketing asset and the thing that
keeps us honest about whether the gateway is actually pleasant to build on.

    A1 publisher   — drafts editorial content and /use pages
    A2 amplifier   — turns published pieces into platform-native drafts
    A3 prospector  — scores buying signals, drafts founder replies
    A4 scorekeeper — weekly payer scorecard and commentary
    A5 concierge   — first-line support, escalates what it cannot cite
    A6 producer    — ad creative; refuses until a channel's CAC clears target

Every agent drafts; none publish. See ``agents.base`` for why.
"""

from agents.base import Agent, AgentRun, GatewayzClient, UsageLedger, UsageRecord

__all__ = [
    "Agent",
    "AgentRun",
    "GatewayzClient",
    "UsageLedger",
    "UsageRecord",
]
