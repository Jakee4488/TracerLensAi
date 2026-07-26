"""Wiring-invariant tests for the causal ADK pipeline.

These guard the Vertex constraint that built-in tools (code execution) must
never be mixed with function declarations or structured output on one agent:
every LlmAgent in the tree carries at most ONE of {code_executor,
output_schema, tools}. Constructing agents needs no credentials or network.
"""

import pytest
from google.adk.agents import Agent, BaseAgent, LlmAgent, LoopAgent, SequentialAgent
from google.adk.code_executors import BuiltInCodeExecutor

from src.causal import state_keys as sk
from src.causal.agents import build_causal_pipeline, build_root_agent
from src.causal.models import CausalDecomposition, CausalEstimand, ReplanResult
from src.causal.router import (
    CausalRouterAgent,
    is_causal_request,
    is_web_request,
    strip_marker,
)


@pytest.fixture(scope="module")
def root():
    general = Agent(
        name="GeneralAssistant",
        model="gemini-2.5-flash",
        instruction="test",
        code_executor=BuiltInCodeExecutor(),
    )
    return build_root_agent(general)


def _walk(agent):
    yield agent
    for sub in agent.sub_agents or []:
        yield from _walk(sub)


def test_root_is_deterministic_router_with_two_branches(root):
    assert isinstance(root, CausalRouterAgent)
    assert root.name == "TracerLensAi_Agent"  # A2A card/trace stability
    assert not isinstance(root, LlmAgent)     # zero LLM calls for routing
    assert [type(s).__name__ for s in root.sub_agents] == ["LlmAgent", "SequentialAgent"]


def test_isolation_invariant_no_capability_mixing(root):
    for agent in _walk(root):
        if not isinstance(agent, LlmAgent):
            continue
        capabilities = [
            agent.code_executor is not None,
            agent.output_schema is not None,
            bool(agent.tools),
        ]
        assert sum(capabilities) <= 1, (
            f"{agent.name} mixes capabilities (Vertex rejects built-in tools "
            f"combined with function declarations/schema)"
        )
        assert not agent.sub_agents, f"{agent.name} must not carry sub_agents"


def test_pipeline_shape(root):
    pipeline = root.sub_agents[1]
    assert isinstance(pipeline, SequentialAgent)
    names = [s.name for s in pipeline.sub_agents]
    # Web retrieval leads (skip-gated), then the usual decompose->synth spine.
    assert names == [
        "CausalWebSearch", "CausalWebIngestor", "CausalDecomposer",
        "CausalEstimandSpec", "CausalEstimator", "CausalExecutorLoop",
        "CausalSynthesizer", "CausalFallbackEmitter",
    ]

    loop = pipeline.sub_agents[5]
    assert isinstance(loop, LoopAgent)
    assert loop.max_iterations == sk.LOOP_MAX_ITERATIONS
    assert [s.name for s in loop.sub_agents] == [
        "CausalStepExecutor", "CausalStepController", "CausalReplanner"]


def test_web_search_is_search_only_and_gated(root):
    pipeline = root.sub_agents[1]
    web_search = pipeline.sub_agents[0]
    ingestor = pipeline.sub_agents[1]

    # Search-only LlmAgent: tools ONLY (no code_executor / output_schema), so the
    # Vertex tool-isolation invariant holds; skip-gated on the web toggle.
    assert isinstance(web_search, LlmAgent)
    assert bool(web_search.tools)
    assert web_search.output_schema is None and web_search.code_executor is None
    assert web_search.output_key == sk.KEY_WEB_SEARCH_RAW
    assert web_search.include_contents == "none"
    assert web_search.before_agent_callback is not None

    # Deterministic ingestor: a BaseAgent, never an LlmAgent.
    assert isinstance(ingestor, BaseAgent) and not isinstance(ingestor, LlmAgent)
    assert not ingestor.sub_agents


def test_estimand_stage_is_isolated_and_gated(root):
    pipeline = root.sub_agents[1]
    estimand_spec = pipeline.sub_agents[3]
    estimator = pipeline.sub_agents[4]

    # Spec LLM carries output_schema ONLY (no code executor / tools) and is
    # skip-gated so it costs 0 LLM calls off the effect-estimation path.
    assert isinstance(estimand_spec, LlmAgent)
    assert estimand_spec.output_schema is CausalEstimand
    assert estimand_spec.output_key == sk.KEY_ESTIMAND_SPEC_RAW
    assert estimand_spec.code_executor is None and not estimand_spec.tools
    assert estimand_spec.include_contents == "none"
    assert estimand_spec.before_agent_callback is not None

    # The estimator runs DoWhy deterministically: a BaseAgent, never an LlmAgent,
    # so it carries no built-in tools and needs no isolation exemption.
    assert isinstance(estimator, BaseAgent)
    assert not isinstance(estimator, LlmAgent)
    assert not estimator.sub_agents


def test_decomposer_and_replanner_are_schema_only(root):
    pipeline = root.sub_agents[1]
    decomposer = pipeline.sub_agents[2]
    loop = pipeline.sub_agents[5]
    executor, _, replanner = loop.sub_agents

    assert decomposer.output_schema is CausalDecomposition
    assert decomposer.output_key == sk.KEY_DECOMPOSITION_RAW
    assert decomposer.code_executor is None and not decomposer.tools

    assert replanner.output_schema is ReplanResult
    assert replanner.output_key == sk.KEY_REPLAN_RAW
    assert replanner.code_executor is None and not replanner.tools
    assert replanner.include_contents == "none"
    assert replanner.before_agent_callback is not None  # skip-guard: 0 LLM on happy path

    assert isinstance(executor.code_executor, BuiltInCodeExecutor)
    assert executor.output_schema is None and not executor.tools
    assert executor.include_contents == "none"
    assert executor.output_key == sk.KEY_STEP_OUTPUT


def test_synthesizer_is_plain(root):
    synthesizer = root.sub_agents[1].sub_agents[6]
    assert synthesizer.output_key == sk.KEY_FINAL
    assert synthesizer.output_schema is None
    assert synthesizer.code_executor is None and not synthesizer.tools


def test_build_causal_pipeline_is_reusable():
    # Factories must not share mutable agent instances between trees.
    p1, p2 = build_causal_pipeline(), build_causal_pipeline()
    assert p1 is not p2
    assert p1.sub_agents[0] is not p2.sub_agents[0]


# ── Semantic effect-gate ──────────────────────────────────────────────────────

def _ctx(state: dict):
    from types import SimpleNamespace
    return SimpleNamespace(state=state)


def test_effect_gate_lexical_or_decomposer_flag():
    from src.causal.callbacks import skip_unless_effect_query

    # Lexical hit alone runs the stage (returns None = don't skip).
    assert skip_unless_effect_query(_ctx({sk.KEY_QUERY: "effect of price on demand?"})) is None

    # Regex miss + no decomposer flag -> skipped.
    miss = "How much would a 10% price rise reduce demand?"
    assert skip_unless_effect_query(_ctx({sk.KEY_QUERY: miss})) is not None

    # Regex miss but the decomposer's semantic flag rescues it.
    dec = {"goal": "g", "components": [], "edges": [], "is_effect_query": True}
    assert skip_unless_effect_query(
        _ctx({sk.KEY_QUERY: miss, sk.KEY_DECOMPOSITION_RAW: dec})) is None

    # Failed decomposition always skips, flag or not.
    assert skip_unless_effect_query(_ctx({
        sk.KEY_QUERY: "effect of x on y",
        sk.KEY_STATUS: {"phase": "failed"},
    })) is not None


# ── Marker helpers ────────────────────────────────────────────────────────────

def test_marker_detection_and_strip():
    assert is_causal_request(f"{sk.CAUSAL_MODE_MARKER} why is revenue down?")
    assert is_causal_request(f"prefix {sk.CAUSAL_MODE_MARKER} suffix")
    assert not is_causal_request("plain question")
    assert not is_causal_request("")
    assert strip_marker(f"{sk.CAUSAL_MODE_MARKER} hello") == "hello"
    assert strip_marker("no marker") == "no marker"


def test_web_marker_detection_and_strip():
    both = f"{sk.CAUSAL_MODE_MARKER} {sk.WEB_MODE_MARKER} effect of x on y?"
    assert is_web_request(both)
    assert not is_web_request(f"{sk.CAUSAL_MODE_MARKER} no web here")
    # Both markers are stripped, leaving the clean query.
    assert strip_marker(both) == "effect of x on y?"


def test_web_skip_gate():
    from src.causal.callbacks import skip_unless_web_requested

    # Off by default -> skip; on -> run.
    assert skip_unless_web_requested(_ctx({})) is not None
    assert skip_unless_web_requested(_ctx({sk.KEY_WEB_REQUESTED: True})) is None
    assert skip_unless_web_requested(_ctx({
        sk.KEY_WEB_REQUESTED: True, sk.KEY_STATUS: {"phase": "failed"}})) is not None
