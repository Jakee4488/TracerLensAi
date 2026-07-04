import math
import re
from typing import Any, Dict, List, Tuple


FILLER_PATTERNS = {
    "please": r"\bplease\b",
    "kindly": r"\bkindly\b",
    "just": r"\bjust\b",
    "really": r"\breally\b",
    "very": r"\bvery\b",
    "actually": r"\bactually\b",
    "basically": r"\bbasically\b",
    "can you": r"\bcan you\b",
    "could you": r"\bcould you\b",
    "i want you to": r"\bi want you to\b",
    "i need you to": r"\bi need you to\b",
}

VAGUE_TERMS = (
    "good",
    "better",
    "best",
    "nice",
    "things",
    "stuff",
    "etc",
    "comprehensive",
    "detailed",
    "robust",
    "quickly",
)

ACTION_VERBS = (
    "analyze",
    "build",
    "classify",
    "compare",
    "create",
    "draft",
    "extract",
    "generate",
    "identify",
    "list",
    "optimize",
    "rewrite",
    "summarize",
    "translate",
    "validate",
)

OUTPUT_FORMAT_TERMS = (
    "json",
    "table",
    "bullets",
    "bullet",
    "markdown",
    "csv",
    "schema",
    "format",
    "return",
    "output",
)

EXTERNAL_CONTEXT_TERMS = (
    "current",
    "latest",
    "today",
    "database",
    "api",
    "file",
    "url",
    "website",
    "search",
    "lookup",
    "fetch",
)

TYPO_NORMALIZATIONS = {
    "buidl": "build",
    "calulator": "calculator",
    "calcualtor": "calculator",
    "caluclator": "calculator",
    "holidy": "holiday",
    "summery": "summary",
    "summarise": "summarize",
    "analize": "analyze",
    "analys": "analyze",
    "optimise": "optimize",
    "optmize": "optimize",
    "recomend": "recommend",
    "reccomend": "recommend",
    "reponse": "response",
    "responce": "response",
}


def analyze_prompt(original_prompt: str) -> Dict[str, Any]:
    prompt = original_prompt.strip()
    normalized_prompt = normalize_typos(prompt)
    token_count = estimate_tokens(prompt)
    filler_hits = _count_pattern_hits(prompt, FILLER_PATTERNS)
    repeated_terms = _find_repeated_terms(normalized_prompt)
    vague_hits = _count_vague_terms(normalized_prompt)
    typo_hits = _find_typo_hits(prompt)
    has_action = _contains_any(normalized_prompt, ACTION_VERBS)
    has_output_shape = _contains_any(normalized_prompt, OUTPUT_FORMAT_TERMS)
    requires_external_context = _contains_any(normalized_prompt, EXTERNAL_CONTEXT_TERMS)

    nodes = _simulate_workflow_nodes(
        prompt=prompt,
        token_count=token_count,
        filler_count=sum(filler_hits.values()),
        repeated_count=len(repeated_terms),
        requires_external_context=requires_external_context,
        has_output_shape=has_output_shape,
    )
    tips = _build_optimization_tips(
        token_count=token_count,
        filler_hits=filler_hits,
        repeated_terms=repeated_terms,
        vague_hits=vague_hits,
        typo_hits=typo_hits,
        has_action=has_action,
        has_output_shape=has_output_shape,
        requires_external_context=requires_external_context,
    )
    optimized_prompt = optimize_prompt(prompt, has_output_shape)
    efficiency_score = _score_efficiency(
        token_count=token_count,
        filler_count=sum(filler_hits.values()),
        repeated_count=len(repeated_terms),
        vague_count=sum(vague_hits.values()),
        has_action=has_action,
        has_output_shape=has_output_shape,
        workflow_nodes=len(nodes),
    )

    return {
        "original_prompt": prompt,
        "simulated_workflow_nodes": nodes,
        "efficiency_score": efficiency_score,
        "optimization_tips": tips,
        "optimized_prompt": optimized_prompt,
    }


def estimate_tokens(text: str) -> int:
    compact_text = re.sub(r"\s+", " ", text.strip())
    if not compact_text:
        return 0
    word_like_tokens = re.findall(r"\w+|[^\w\s]", compact_text)
    character_estimate = math.ceil(len(compact_text) / 4)
    return max(1, round((len(word_like_tokens) + character_estimate) / 2))


def optimize_prompt(prompt: str, has_output_shape: bool = False) -> str:
    optimized = normalize_typos(prompt.strip())
    replacements = (
        (r"\bdue to the fact that\b", "because"),
        (r"\bin order to\b", "to"),
        (r"\bat this point in time\b", "now"),
        (r"\bwith regard to\b", "about"),
        (r"\bfor the purpose of\b", "to"),
    )
    for pattern, replacement in replacements:
        optimized = re.sub(pattern, replacement, optimized, flags=re.IGNORECASE)

    for pattern in FILLER_PATTERNS.values():
        optimized = re.sub(pattern, "", optimized, flags=re.IGNORECASE)

    optimized = _collapse_repeated_words(optimized)
    optimized = re.sub(r"\s+([,.!?;:])", r"\1", optimized)
    optimized = re.sub(r"\s+", " ", optimized).strip(" ,")
    optimized = _sentence_case(optimized)

    if has_output_shape:
        return optimized
    return f"{optimized}\n\nOutput: concise bullets with only necessary context."


def normalize_typos(text: str) -> str:
    normalized = text
    for typo, correction in TYPO_NORMALIZATIONS.items():
        normalized = re.sub(
            rf"\b{re.escape(typo)}\b",
            lambda match: _preserve_case(match.group(0), correction),
            normalized,
            flags=re.IGNORECASE,
        )
    return normalized


def _simulate_workflow_nodes(
    prompt: str,
    token_count: int,
    filler_count: int,
    repeated_count: int,
    requires_external_context: bool,
    has_output_shape: bool,
) -> List[Dict[str, Any]]:
    nodes = [
        _node("input", "Input capture", "Raw prompt received and tokenized.", token_count, 5.0, 0),
        _node("intent", "Intent parsing", "Infer the user's task, constraints, and missing details.", _bounded_tokens(token_count, 0.22, 8), 28.0, 1),
    ]

    if filler_count or repeated_count or token_count > 40:
        nodes.append(
            _node(
                "pruning",
                "Context pruning",
                "Remove redundant phrasing before downstream reasoning.",
                _bounded_tokens(token_count, 0.18, 6),
                18.0,
                len(nodes),
            )
        )
    else:
        nodes.append(
            _node(
                "validation",
                "Constraint check",
                "Confirm that the prompt has enough actionable context.",
                _bounded_tokens(token_count, 0.12, 5),
                14.0,
                len(nodes),
            )
        )

    if requires_external_context:
        nodes.append(
            _node(
                "retrieval",
                "Retrieval planning",
                "Plan required API, file, search, or database access.",
                _bounded_tokens(token_count, 0.2, 10),
                45.0,
                len(nodes),
            )
        )
    else:
        nodes.append(
            _node(
                "reasoning",
                "Reasoning plan",
                "Select the shortest path to answer the task.",
                _bounded_tokens(token_count, 0.16, 8),
                32.0,
                len(nodes),
            )
        )

    if not has_output_shape:
        nodes.append(
            _node(
                "format",
                "Output shaping",
                "Choose a compact response format because none was specified.",
                _bounded_tokens(token_count, 0.12, 6),
                20.0,
                len(nodes),
            )
        )

    nodes.append(
        _node(
            "response",
            "Response synthesis",
            "Generate the final answer using the reduced instruction set.",
            _bounded_tokens(token_count, 0.3, 12),
            38.0,
            len(nodes),
        )
    )
    return nodes


def _node(node_id: str, label: str, description: str, tokens: int, latency_ms: float, order: int) -> Dict[str, Any]:
    return {
        "id": node_id,
        "label": label,
        "stage": node_id,
        "description": description,
        "tokens": tokens,
        "latency_ms": latency_ms,
        "position": {"x": 40 + (order * 180), "y": 48},
    }


def _bounded_tokens(token_count: int, ratio: float, minimum: int) -> int:
    return max(minimum, math.ceil(token_count * ratio))


def _build_optimization_tips(
    token_count: int,
    filler_hits: Dict[str, int],
    repeated_terms: List[str],
    vague_hits: Dict[str, int],
    typo_hits: Dict[str, str],
    has_action: bool,
    has_output_shape: bool,
    requires_external_context: bool,
) -> List[str]:
    tips = []
    if typo_hits:
        examples = [f"{typo} -> {correction}" for typo, correction in list(typo_hits.items())[:3]]
        tips.append(f"Correct likely typos such as {', '.join(examples)}.")
    filler_terms = [term for term, count in filler_hits.items() if count]
    if filler_terms:
        tips.append(f"Remove filler phrasing such as {', '.join(filler_terms[:3])}.")
    if repeated_terms:
        tips.append("Collapse repeated words or duplicated requirements into one instruction.")
    if token_count > 45:
        tips.append("Lead with the task, then keep only context that changes the answer.")
    if vague_hits:
        tips.append("Replace vague qualifiers with concrete success criteria or limits.")
    if not has_action:
        tips.append("Start with a precise action verb so the workflow skips intent discovery.")
    if not has_output_shape:
        tips.append("State the expected output shape once to prevent verbose response synthesis.")
    if requires_external_context:
        tips.append("Name the required data source or tool explicitly to reduce retrieval planning.")
    if not tips:
        tips.append("The prompt is compact; keep future additions tied to task, context, constraints, or output.")
    return tips


def _score_efficiency(
    token_count: int,
    filler_count: int,
    repeated_count: int,
    vague_count: int,
    has_action: bool,
    has_output_shape: bool,
    workflow_nodes: int,
) -> int:
    score = 100
    score -= min(25, max(0, token_count - 40) // 2)
    score -= min(20, filler_count * 4)
    score -= min(15, repeated_count * 3)
    score -= min(18, vague_count * 3)
    score -= 10 if not has_action else 0
    score -= 6 if not has_output_shape else 0
    score -= max(0, workflow_nodes - 5) * 3
    return max(1, min(100, score))


def _count_pattern_hits(text: str, patterns: Dict[str, str]) -> Dict[str, int]:
    return {name: len(re.findall(pattern, text, flags=re.IGNORECASE)) for name, pattern in patterns.items()}


def _find_typo_hits(text: str) -> Dict[str, str]:
    hits = {}
    for typo, correction in TYPO_NORMALIZATIONS.items():
        if re.search(rf"\b{re.escape(typo)}\b", text, flags=re.IGNORECASE):
            hits[typo] = correction
    return hits


def _find_repeated_terms(text: str) -> List[str]:
    words = [word.lower() for word in re.findall(r"\b[a-zA-Z][a-zA-Z'-]*\b", text)]
    repeated = []
    for previous, current in zip(words, words[1:]):
        if previous == current and current not in repeated:
            repeated.append(current)
    return repeated


def _count_vague_terms(text: str) -> Dict[str, int]:
    normalized = text.lower()
    return {term: len(re.findall(rf"\b{re.escape(term)}\b", normalized)) for term in VAGUE_TERMS if re.search(rf"\b{re.escape(term)}\b", normalized)}


def _contains_any(text: str, terms: Tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(re.search(rf"\b{re.escape(term)}\b", normalized) for term in terms)


def _collapse_repeated_words(text: str) -> str:
    previous = None
    collapsed = text
    while previous != collapsed:
        previous = collapsed
        collapsed = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", collapsed, flags=re.IGNORECASE)
    return collapsed


def _sentence_case(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def _preserve_case(source: str, correction: str) -> str:
    if source.isupper():
        return correction.upper()
    if source[0].isupper():
        return correction.capitalize()
    return correction
