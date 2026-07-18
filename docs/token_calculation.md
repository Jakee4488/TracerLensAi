# Token Calculation and Compounding Analysis

Understanding how tokens accumulate — both within a single multi-agent turn and across a long conversation — is critical for cost and latency. This document has two parts: **how TracerLensAi actually measures tokens today**, and the **compounding model** that explains why agentic workflows grow expensive.

## How Tokens Are Measured Today

Token usage is measured directly from the Agent Engine's streamed events, not estimated. In [`proxy/main.py`](../proxy/main.py), `analyze_prompt` reads each streamed event's `usage_metadata.total_token_count` (snake_case or camelCase) and **sums** them across the whole turn:

```python
usage = event.get("usage_metadata") or event.get("usageMetadata")
if isinstance(usage, dict):
    count = usage.get("total_token_count", usage.get("totalTokenCount"))
    if isinstance(count, int):
        total_token_count += count
```

This matters most in **causal mode**: a single turn fans out into multiple LLM calls — decompose (1) + up to `max_steps` executor calls + up to `max_replans` replanner calls + synthesize (1) — and ADK emits a `usage_metadata` block per call. Summing them yields the true per-turn total the UI shows in its token badge. The proxy returns this as `total_token_count`, the frontend adds it to the running `sessionTotalTokens`, and for signed-in users it is accumulated onto the Firestore conversation via `total_tokens` (`gcf.Increment`).

> The number of executor/replanner calls — and therefore the token total — is
> bounded by the causal budgets (`CAUSAL_MAX_STEPS`, `CAUSAL_MAX_REPLANS`) and
> sized per query by [`src/causal/complexity.py`](../src/causal/complexity.py).
> See [Causal Reasoning](causal_reasoning.md).

## Why Tokens Compound (Background Model)

The Agent Engine retains conversation context across turns, so the mathematical compounding of tokens still applies when the agent reasons over long histories. The model below explains the growth pattern; it is analytical background rather than a module in this repo.
