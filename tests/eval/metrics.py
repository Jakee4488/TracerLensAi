"""Local LLM-as-judge for `agents-cli eval grade`, wired in from
tests/eval/eval_config.yaml (`custom_function_file: metrics.py`).

Instead of one flat 1-5, this grades the agent's final response on FOUR causal
reasoning axes and aggregates them into the single `score` the CLI expects,
weighting *causal correctness* highest (a fluent answer that gets the causal
claim wrong must not score well). The per-axis breakdown is returned in
`explanation` so a failing case tells you *which* axis to fix.

Axes (each 1-5):
  - causal_correctness: right causal claim — confounders, mediators, direction,
    adjustment set, effect sign/magnitude. Weighted highest.
  - grounding:          numbers/claims are supported by the trace (code output),
    not fabricated. Penalizes hallucinated quantities.
  - relevance:          answers what was actually asked.
  - clarity:            clear, well-structured, states assumptions.

Runs in-process via google-genai, so it grades on BOTH Vertex (ADC) and Google
AI Studio (GEMINI_API_KEY) with no managed Vertex eval service -- genai.Client()
auto-selects the backend from the project's .env (loaded by `eval grade`). It
uses each case's `reference` (ground truth) when present.
"""

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# Weights sum to 1.0; causal correctness dominates so a wrong causal claim caps
# the aggregate low even when the prose is polished.
_WEIGHTS = {
    "causal_correctness": 0.55,
    "grounding": 0.20,
    "relevance": 0.15,
    "clarity": 0.10,
}


class _Verdict(BaseModel):
    causal_correctness: int = Field(ge=1, le=5)
    grounding: int = Field(ge=1, le=5)
    relevance: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    explanation: str


def _clamp(v: int) -> int:
    return max(1, min(5, int(v)))


def evaluate(instance):
    reference = instance.get("reference")
    rubric = (
        "You are an expert evaluator of CAUSAL REASONING for an enterprise AI "
        "assistant (TracerLensAi). Score the agent's final response on four axes, "
        "each an integer 1-5 (1 poor, 5 excellent):\n"
        "- causal_correctness: Is the causal claim right? Correctly distinguishes "
        "correlation from causation; identifies confounders/common causes; does NOT "
        "adjust for mediators or colliders; gets the direction, adjustment set, and "
        "the sign/magnitude of any effect right. This is the most important axis.\n"
        "- grounding: Are quantitative claims and stated results supported by the "
        "agent's trace (e.g., code-execution output)? Penalize fabricated numbers or "
        "results not present in the trace.\n"
        "- relevance: Does it answer what was actually asked?\n"
        "- clarity: Is it clear, well-structured, and explicit about assumptions?"
    )
    if reference:
        rubric += (
            "\nA reference (ground-truth) answer is provided. Penalize "
            "causal_correctness heavily for factual disagreement with it."
        )
    prompt = (
        f"{rubric}\n\n"
        f"User Prompt:\n{instance.get('prompt', '')}\n\n"
        f"Agent Final Response:\n{instance.get('response', '')}\n\n"
    )
    if reference:
        prompt += f"Reference (ground truth):\n{reference}\n\n"
    prompt += (
        "Full Agent Trace (tool calls, code execution, intermediate reasoning):\n"
        f"{instance.get('agent_data', '')}\n\n"
        "Return JSON with integer fields causal_correctness, grounding, relevance, "
        "clarity, and a short explanation citing the weakest axis."
    )

    client = genai.Client()  # AI Studio (GEMINI_API_KEY) or Vertex (ADC)
    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,  # deterministic grading
            response_mime_type="application/json",
            response_schema=_Verdict,  # guaranteed schema-valid JSON
        ),
    )
    verdict = response.parsed
    if verdict is None:  # model returned nothing usable
        return {"score": 0, "explanation": response.text or "judge returned no verdict"}

    axes = {
        "causal_correctness": _clamp(verdict.causal_correctness),
        "grounding": _clamp(verdict.grounding),
        "relevance": _clamp(verdict.relevance),
        "clarity": _clamp(verdict.clarity),
    }
    aggregate = sum(axes[name] * w for name, w in _WEIGHTS.items())
    breakdown = ", ".join(f"{name}={axes[name]}" for name in _WEIGHTS)
    return {
        "score": round(aggregate, 2),  # weighted 1-5, causal correctness dominant
        "explanation": f"[{breakdown}] {verdict.explanation}",
    }
