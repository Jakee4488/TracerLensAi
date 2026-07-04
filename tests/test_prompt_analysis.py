from fastapi.testclient import TestClient

from src.main import app
from src.observability.prompt_analysis import analyze_prompt


client = TestClient(app)


def test_prompt_analysis_engine_returns_required_payload():
    prompt = (
        "Please please can you just write a very very detailed detailed summary "
        "of the uploaded file and make it good"
    )

    result = analyze_prompt(prompt)

    assert result["original_prompt"] == prompt
    assert result["simulated_workflow_nodes"]
    assert isinstance(result["efficiency_score"], int)
    assert 1 <= result["efficiency_score"] <= 100
    assert result["optimization_tips"]
    assert "please please" not in result["optimized_prompt"].lower()


def test_prompt_analysis_rewards_concise_structured_prompt():
    verbose = "Please can you just make a very detailed and comprehensive and detailed answer about deployment stuff"
    concise = "Summarize deployment risks as five bullets."

    verbose_result = analyze_prompt(verbose)
    concise_result = analyze_prompt(concise)

    assert concise_result["efficiency_score"] > verbose_result["efficiency_score"]
    assert len(concise_result["simulated_workflow_nodes"]) <= len(verbose_result["simulated_workflow_nodes"])


def test_prompt_analysis_normalizes_common_typos():
    result = analyze_prompt("buidl a holiday calulator")

    assert result["optimized_prompt"].startswith("Build a holiday calculator")
    assert any("buidl -> build" in tip for tip in result["optimization_tips"])
    assert not any("Start with a precise action verb" in tip for tip in result["optimization_tips"])


def test_analyze_prompt_endpoint_response_schema():
    response = client.post(
        "/analyze-prompt",
        json={"prompt": "Analyze the latest API logs and return JSON with errors and suggested fixes."},
    )

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {
        "original_prompt",
        "simulated_workflow_nodes",
        "efficiency_score",
        "optimization_tips",
        "optimized_prompt",
    }
    assert data["original_prompt"].startswith("Analyze the latest API logs")
    assert data["simulated_workflow_nodes"][0]["label"] == "Input capture"
    assert isinstance(data["optimization_tips"], list)


def test_analyze_prompt_endpoint_rejects_empty_prompt():
    response = client.post("/analyze-prompt", json={"prompt": "   "})

    assert response.status_code == 422
    assert response.json()["detail"] == "Prompt cannot be empty"


def test_static_page_includes_prompt_analysis_component():
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="prompt-analysis-form"' in response.text
    assert 'id="prompt-analysis-panel"' in response.text
    assert 'aria-controls="graph-panel prompt-analysis-panel"' in response.text
    assert "Prompt Optimiser" in response.text
    assert "Workflow Optimiser" in response.text
    assert 'id="workflow-chat-panel"' in response.text
    assert 'id="workflow-chat-input"' in response.text
    assert 'id="workflow-send-btn"' in response.text
    assert "/static/prompt-analysis.js" in response.text
    assert "/static/workflow-optimizer.js" in response.text


def test_workflow_optimizer_script_toggles_between_panels():
    script = client.get("/static/workflow-optimizer.js")

    assert script.status_code == 200
    assert "function setOptimiserView(showWorkflowOptimiser)" in script.text
    assert 'document.body.classList.toggle("workflow-mode", showWorkflowOptimiser)' in script.text
    assert 'promptPanel.hidden = showWorkflowOptimiser' in script.text
    assert 'showWorkflowOptimiser ? "Prompt Optimiser" : "Workflow Optimiser"' in script.text
    assert "async function sendWorkflowMessage()" in script.text
    assert 'fetch("/inquire-traced"' in script.text
    assert "Send a message first so a trace is available to optimise." not in script.text
