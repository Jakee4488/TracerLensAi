document.addEventListener("DOMContentLoaded", () => {
    const graphPanel = document.getElementById("graph-panel");
    const toggleBtn = document.getElementById("toggle-graph");
    const closeBtn = document.getElementById("close-graph");
    const canvas = document.getElementById("decision-graph");
    const ctx = canvas.getContext("2d");
    
    // Evaluation elements
    const evalBtn = document.getElementById("evaluate-btn");
    const evalResults = document.getElementById("evaluator-results");
    
    let currentTrace = null;
    let currentPrompt = "";
    let sessionPromptTokens = 0;
    let sessionCompletionTokens = 0;

    // Toggle logic
    toggleBtn.addEventListener("click", () => graphPanel.classList.add("open"));
    closeBtn.addEventListener("click", () => graphPanel.classList.remove("open"));

    // Handle new trace data from chat.js
    window.addEventListener("newTraceData", (e) => {
        currentPrompt = e.detail.prompt;
        currentTrace = e.detail.trace;
        
        // Accumulate tokens
        const pTokens = e.detail.metrics.prompt_tokens || 0;
        const cTokens = e.detail.metrics.completion_tokens || 0;
        sessionPromptTokens += pTokens;
        sessionCompletionTokens += cTokens;
        
        // Update Token Panel
        document.getElementById("prompt-tokens-total").innerText = sessionPromptTokens;
        document.getElementById("completion-tokens-total").innerText = sessionCompletionTokens;
        document.getElementById("total-tokens").innerText = sessionPromptTokens + sessionCompletionTokens;
        
        // Reset evaluator panel
        evalResults.style.display = "none";
        
        // Draw the graph
        drawGraph(currentTrace);
    });

    // Handle reset session
    window.addEventListener("resetSession", () => {
        currentTrace = null;
        currentPrompt = "";
        sessionPromptTokens = 0;
        sessionCompletionTokens = 0;
        
        document.getElementById("prompt-tokens-total").innerText = "0";
        document.getElementById("completion-tokens-total").innerText = "0";
        document.getElementById("total-tokens").innerText = "0";
        
        evalResults.style.display = "none";
        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    });

    // Handle Evaluator
    evalBtn.addEventListener("click", async () => {
        if (!currentTrace) {
            alert("No trace available to evaluate.");
            return;
        }

        const btnOriginalText = evalBtn.innerText;
        evalBtn.innerText = "Analyzing Trace...";
        evalBtn.disabled = true;

        try {
            const response = await fetch("/evaluate-trace", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    original_prompt: currentPrompt,
                    ideal_steps: 3, // Hardcoded for this demo 
                    trace: currentTrace
                })
            });

            const data = await response.json();
            
            // Populate UI
            document.getElementById("eval-efficiency").innerText = data.dashboard.step_efficiency_ratio;
            document.getElementById("eval-loop").innerText = data.dashboard.loop_rate;
            document.getElementById("eval-velocity").innerText = data.dashboard.token_velocity;
            document.getElementById("eval-bottleneck").innerText = data.dashboard.latency_bottleneck;
            
            const rcaUl = document.getElementById("eval-rca");
            rcaUl.innerHTML = "";
            (data.root_cause_analysis || []).forEach(item => {
                const li = document.createElement("li");
                li.innerText = item;
                rcaUl.appendChild(li);
            });
            
            document.getElementById("eval-prompt").value = data.rectified_prompt;
            
            evalResults.style.display = "block";

        } catch (error) {
            console.error("Evaluation failed", error);
            alert("Failed to evaluate the workflow.");
        } finally {
            evalBtn.innerText = btnOriginalText;
            evalBtn.disabled = false;
        }
    });

    // Resize canvas
    function resizeCanvas() {
        const container = canvas.parentElement;
        canvas.width = container.clientWidth;
        canvas.height = container.clientHeight;
        if (currentTrace) drawGraph(currentTrace);
    }
    window.addEventListener("resize", resizeCanvas);
    setTimeout(resizeCanvas, 100);

    // Simple Node Drawer
    function drawGraph(trace) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        if (!trace || trace.length === 0) return;

        const nodeWidth = 200;
        const nodeHeight = 50;
        const startX = canvas.width / 2 - nodeWidth / 2;
        let startY = 30;
        const ySpacing = 90;

        for (let i = 0; i < trace.length; i++) {
            const step = trace[i];
            
            // Draw connector line from previous
            if (i > 0) {
                ctx.beginPath();
                ctx.moveTo(startX + nodeWidth/2, startY - ySpacing + nodeHeight);
                ctx.lineTo(startX + nodeWidth/2, startY);
                ctx.strokeStyle = "#4285f4";
                ctx.lineWidth = 2;
                ctx.setLineDash([5, 5]);
                ctx.stroke();
                ctx.setLineDash([]);
                
                // Draw tokens label on line
                if (step.tokens > 0) {
                    ctx.fillStyle = "#9aa0a6";
                    ctx.font = "10px Inter";
                    ctx.fillText(`${step.tokens} tokens`, startX + nodeWidth/2 + 10, startY - ySpacing/2);
                }
            }

            // Draw Node Box
            let bgColor = "#1e1f20";
            let borderColor = "#444746";
            
            if (step.step_type === "llm_call") borderColor = "#9b72cb"; // Purple
            if (step.step_type === "tool_call") borderColor = "#f59e0b"; // Amber
            if (step.step_type === "response") borderColor = "#00ff88"; // Green
            if (step.step_type === "escalation") borderColor = "#f97316"; // Orange

            ctx.fillStyle = bgColor;
            ctx.beginPath();
            ctx.roundRect(startX, startY, nodeWidth, nodeHeight, 8);
            ctx.fill();
            
            ctx.strokeStyle = borderColor;
            ctx.lineWidth = 1;
            ctx.stroke();

            // Draw Text
            ctx.fillStyle = "#e3e3e3";
            ctx.font = "12px Inter";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(step.description.substring(0, 30), startX + nodeWidth/2, startY + nodeHeight/2 - 6);
            
            ctx.fillStyle = "#9aa0a6";
            ctx.font = "10px Inter";
            ctx.fillText(`${step.latency_ms.toFixed(0)}ms`, startX + nodeWidth/2, startY + nodeHeight/2 + 10);

            startY += ySpacing;
        }
    }
});
