"""Causal reasoning pathway engine.

Deterministic causal-graph planning, execution tracking, impact propagation,
and localized replanning for the TracerLensAi agent. The pure modules
(state_keys, models, graph_engine, runtime, ledger) have no ADK/Vertex
dependencies; the orchestration modules (prompts, callbacks, controller,
router, agents) wire them into the ADK multi-agent pipeline.
"""
