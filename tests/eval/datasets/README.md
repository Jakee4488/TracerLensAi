# Evaluation Datasets

This directory contains evaluation datasets for testing agent behavior.

## Datasets

- `basic-dataset.json` — sanity + qualitative causal-reasoning cases (general assistant path).
- `causal-inference-dataset.json` — **ground-truth causal-inference cases for the
  `[[causal:on]]` pipeline**: back-door identification with/without data, mediator and
  collider traps, and a counterfactual. The CSV cases embed data generated from known
  SCMs (true ATE 2.0; price slope −1.5), so the judge's `reference` carries real ground
  truth — regenerate via the SCM recipe in the references if the data ever needs
  refreshing. The deterministic (no-LLM) counterpart of these checks lives in
  `tests/test_causal_benchmark.py`.
- `causal-traps-dataset.json` — **10 discrimination cases built to break a wrong
  agent.** Four trap families (confounder / mediator / collider / spurious), each from
  a known SCM with 70 rows. Every case carries **numeric** ground truth (the true
  effect) *and* **structural** ground truth (which variables belong in the adjustment
  set and which must be excluded), so the deterministic metrics grade them without the
  LLM judge.

  The defining property: **the naive answer falls outside the tolerance band while the
  correct adjusted estimate falls inside.** Three cases are sign flips — the naive
  estimate has the wrong sign, not merely the wrong magnitude. A case that both a
  broken and a correct agent pass is not a trap, so
  `tests/test_eval_assertions.py::test_correct_agent_passes_and_naive_agent_fails`
  asserts both halves for every case and will fail if a tolerance is ever widened
  until a trap stops trapping. Each entry in `../expectations.json` records
  `_naive_estimate` and `_correct_estimate` so the band can be re-checked by hand.

  These need node traces — run generate with `CAUSAL_NODE_TRACE=1`.

## Running Evaluations

### Default Dataset
```bash
# Generate traces using the default dataset
agents-cli eval generate
agents-cli eval grade
```

### Custom Dataset
```bash
# Generate traces for a custom dataset
agents-cli eval generate --dataset tests/eval/datasets/custom-dataset.json --output custom_traces/
agents-cli eval grade --metrics general_quality --traces custom_traces/
```

## Dataset Format

Each dataset file follows the Gemini Enterprise Agent Platform Evaluation
dataset format. An eval case may use **either** of two shapes — both are
valid input to `agents-cli eval generate`:

**Shape A — single-prompt case:**

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "prompt": {
        "role": "user",
        "parts": [{"text": "User message"}]
      }
    }
  ]
}
```

**Shape B — continued-conversation case (the "N+1" pattern):**
The case carries prior turns in `agent_data` and the last turn ends with a
user message; `eval generate` appends the next agent response.

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "agent_data": {
        "turns": [
          {
            "turn_index": 0,
            "events": [
              {"author": "user",  "content": {"role": "user",  "parts": [{"text": "First user message"}]}},
              {"author": "agent", "content": {"role": "model", "parts": [{"text": "First agent reply"}]}},
              {"author": "user",  "content": {"role": "user",  "parts": [{"text": "Follow-up user message"}]}}
            ]
          }
        ]
      }
    }
  ]
}
```

## Key Fields

- `eval_cases`: Array of evaluation cases.
- `eval_case_id`: Unique identifier for the evaluation case (optional).
- `prompt`: A single user message — Shape A.
- `agent_data.turns`: Prior conversation turns ending with a user message — Shape B.

## Creating Custom Datasets

You can create custom datasets in two ways:

1. **By Hand**: Copy `basic-dataset.json` as a template and manually add evaluation cases.
2. **Synthesize**: Use the synthetic dataset generation command to generate conversation scenarios:
   ```bash
   agents-cli eval dataset synthesize --count 10
   ```

## Discovering Metrics

You can discover available out-of-the-box evaluation metrics by running:

```bash
agents-cli eval metric list
```

## Beyond Generate and Grade

Once you have a baseline, the eval surface has a few more commands worth knowing about:

- `agents-cli eval compare BASE CAND` — diff two grade-results files (regression check).
- `agents-cli eval analyze RESULTS` — cluster failure modes from a grade-results file.
- `agents-cli eval optimize` — auto-tune your agent's prompts using eval data.

See the [Evaluation Guide](https://google.github.io/agents-cli/guide/evaluation/) for the full surface and metric reference.
