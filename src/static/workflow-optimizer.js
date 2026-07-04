/**
 * workflow-optimizer.js
 *
 * Replaces decision-graph.js.  Manages the right-side Workflow Optimiser
 * panel including the compounding token chart, projection summary, merged
 * optimisation tips, workflow steps, and golden dataset plan.
 *
 * Also retains the inline trace canvas rendering inside chat bubbles.
 */
document.addEventListener("DOMContentLoaded", () => {
    const graphPanel = document.getElementById("graph-panel");
    const promptPanel = document.getElementById("prompt-analysis-panel");
    const toggleBtn = document.getElementById("toggle-graph");
    const closeBtn = document.getElementById("close-graph");

    // Optimiser trigger
    const optimiseBtn = document.getElementById("optimise-btn");
    const workflowInput = document.getElementById("workflow-chat-input");
    const workflowSendBtn = document.getElementById("workflow-send-btn");
    const workflowMessages = document.getElementById("workflow-messages-area");
    const workflowTraceStatus = document.getElementById("workflow-trace-status");

    let currentTrace = null;
    let currentPrompt = "";
    let sessionPromptTokens = 0;
    let sessionCompletionTokens = 0;

    // ── Global state for inline trace canvases ──────────────────────────────
    const canvasInstances = {};

    // ── Toggle logic ────────────────────────────────────────────────────────
    toggleBtn.addEventListener("click", () => {
        setOptimiserView(!graphPanel.classList.contains("open"));
    });
    closeBtn.addEventListener("click", () => setOptimiserView(false));

    function setOptimiserView(showWorkflowOptimiser) {
        document.body.classList.toggle("workflow-mode", showWorkflowOptimiser);
        graphPanel.classList.toggle("open", showWorkflowOptimiser);
        if (promptPanel) {
            promptPanel.hidden = showWorkflowOptimiser;
        }

        toggleBtn.setAttribute("aria-expanded", String(showWorkflowOptimiser));
        toggleBtn.textContent = showWorkflowOptimiser ? "Prompt Optimiser" : "Workflow Optimiser";
        closeBtn.textContent = showWorkflowOptimiser ? "Prompt Optimiser" : "×";
    }

    // ── Listen for new trace data from chat.js ──────────────────────────────
    window.addEventListener("newTraceData", (e) => {
        currentPrompt = e.detail.prompt;
        currentTrace = e.detail.trace;
        const canvasId = e.detail.canvasId;

        updateSessionTokenTotals(e.detail.metrics || {});

        // Reset optimiser results
        resetOptimiserResults();

        // Initialise inline trace canvas in chat bubble
        const canvas = document.getElementById(canvasId);
        if (canvas) {
            setupCanvasInteractivity(canvas, canvasId);
            canvasInstances[canvasId] = { trace: currentTrace, nodeBounds: [], currentVisibleNodes: 0 };

            const rect = canvas.parentElement.getBoundingClientRect();
            canvas.width = rect.width;
            canvas.height = rect.height;
            animateGraph(canvasId);
        }
    });

    // ── Reset session ───────────────────────────────────────────────────────
    window.addEventListener("resetSession", () => {
        currentTrace = null;
        currentPrompt = "";
        sessionPromptTokens = 0;
        sessionCompletionTokens = 0;

        document.getElementById("prompt-tokens-total").innerText = "0";
        document.getElementById("completion-tokens-total").innerText = "0";
        document.getElementById("total-tokens").innerText = "0";

        resetOptimiserResults();
    });

    function updateSessionTokenTotals(metrics) {
        const pTokens = metrics.prompt_tokens || 0;
        const cTokens = metrics.completion_tokens || 0;
        sessionPromptTokens += pTokens;
        sessionCompletionTokens += cTokens;

        document.getElementById("prompt-tokens-total").innerText = sessionPromptTokens;
        document.getElementById("completion-tokens-total").innerText = sessionCompletionTokens;
        document.getElementById("total-tokens").innerText = sessionPromptTokens + sessionCompletionTokens;
    }

    function appendWorkflowMessage(text, sender) {
        const message = document.createElement("div");
        message.className = `workflow-message ${sender}`;
        message.textContent = text;
        workflowMessages.appendChild(message);
        workflowMessages.scrollTop = workflowMessages.scrollHeight;
    }

    function appendWorkflowLoading() {
        const id = `workflow-loading-${Date.now()}`;
        const message = document.createElement("div");
        message.id = id;
        message.className = "workflow-message ai";
        message.textContent = "Generating trace...";
        workflowMessages.appendChild(message);
        workflowMessages.scrollTop = workflowMessages.scrollHeight;
        return id;
    }

    function removeWorkflowLoading(id) {
        const loading = document.getElementById(id);
        if (loading) loading.remove();
    }

    function clearWorkflowEmptyState() {
        const empty = workflowMessages.querySelector(".workflow-empty-state");
        if (empty) empty.remove();
    }

    function setWorkflowStatus(text) {
        workflowTraceStatus.textContent = text;
    }

    // ── Optimise Workflow button ─────────────────────────────────────────────
    optimiseBtn.addEventListener("click", () => {
        if (!currentTrace) {
            setWorkflowStatus("Send a workflow prompt first");
            workflowInput.focus();
            return;
        }
        triggerOptimisation();
    });

    workflowSendBtn.addEventListener("click", sendWorkflowMessage);
    workflowInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendWorkflowMessage();
        }
    });

    workflowInput.addEventListener("input", function() {
        this.style.height = "auto";
        this.style.height = `${this.scrollHeight}px`;
    });

    async function sendWorkflowMessage() {
        const prompt = workflowInput.value.trim();
        if (!prompt) {
            setWorkflowStatus("Add a workflow prompt");
            return;
        }

        workflowInput.value = "";
        workflowInput.style.height = "auto";
        clearWorkflowEmptyState();
        appendWorkflowMessage(prompt, "user");
        const loadingId = appendWorkflowLoading();
        workflowSendBtn.disabled = true;
        setWorkflowStatus("Generating trace");

        try {
            const response = await fetch("/inquire-traced", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ user_id: "workflow_user", prompt }),
            });

            const data = await response.json();
            removeWorkflowLoading(loadingId);
            appendWorkflowMessage(data.response || "Workflow trace generated.", "ai");
            currentPrompt = prompt;
            currentTrace = data.trace || [];
            updateSessionTokenTotals(data.metrics || {});
            resetOptimiserResults();
            setWorkflowStatus(currentTrace.length ? `${currentTrace.length} trace steps ready` : "Trace ready");

            window.dispatchEvent(new CustomEvent("workflowTraceReady", {
                detail: {
                    prompt,
                    trace: currentTrace,
                    metrics: data.metrics || {},
                },
            }));
        } catch (error) {
            removeWorkflowLoading(loadingId);
            appendWorkflowMessage("Sorry, the workflow trace could not be generated.", "ai");
            setWorkflowStatus("Trace failed");
            console.error(error);
        } finally {
            workflowSendBtn.disabled = false;
        }
    }

    async function triggerOptimisation() {
        if (!currentTrace) return;

        const originalText = optimiseBtn.innerText;
        optimiseBtn.innerText = "Analysing…";
        optimiseBtn.disabled = true;

        try {
            const response = await fetch("/optimize-workflow", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    original_prompt: currentPrompt,
                    trace: currentTrace,
                    run_prompt_analysis: true,
                }),
            });

            const data = await response.json();
            renderOptimisation(data);
        } catch (err) {
            console.error("Workflow optimisation failed", err);
            alert("Failed to optimise workflow. Check the console for details.");
        } finally {
            optimiseBtn.innerText = originalText;
            optimiseBtn.disabled = false;
        }
    }

    // ── Render optimisation response ────────────────────────────────────────
    function renderOptimisation(data) {
        const container = document.getElementById("wo-results");

        // Formula
        document.getElementById("wo-formula").innerText = data.compounding_formula || "";

        // Projection stats
        const proj = data.projection || {};
        document.getElementById("wo-est-total").innerText = (proj.estimated_total_tokens || 0).toLocaleString();
        document.getElementById("wo-cost-mult").innerText = `${proj.cost_multiplier || 0}×`;
        document.getElementById("wo-comp-hist").innerText = (proj.compounding_history_tokens || 0).toLocaleString();

        // Risk badge
        const riskBadge = document.getElementById("wo-risk-badge");
        const risk = (proj.risk_level || "low").toLowerCase();
        riskBadge.innerText = risk.charAt(0).toUpperCase() + risk.slice(1);
        riskBadge.className = "wo-risk-badge risk-" + risk;

        // Assumptions table
        const assumptions = data.assumptions || {};
        document.getElementById("wo-loops").innerText = assumptions.expected_loops || "-";
        document.getElementById("wo-base-p").innerText = assumptions.base_prompt_tokens || "-";
        document.getElementById("wo-avg-o").innerText = assumptions.average_model_output_tokens || "-";
        document.getElementById("wo-avg-t").innerText = assumptions.average_tool_output_tokens || "-";

        // Compounding chart
        renderCompoundingChart(data.compounding_chart || []);

        // Optimisation tips (merged)
        const tipsList = document.getElementById("wo-tips-list");
        tipsList.innerHTML = "";
        (data.optimization_tips || []).forEach((tip) => {
            const li = document.createElement("li");
            li.textContent = tip;
            tipsList.appendChild(li);
        });

        // Optimised workflow steps
        const stepsList = document.getElementById("wo-workflow-steps");
        stepsList.innerHTML = "";
        (data.optimized_workflow || []).forEach((step) => {
            const li = document.createElement("li");
            li.textContent = step;
            stepsList.appendChild(li);
        });

        // Golden dataset plan
        const planList = document.getElementById("wo-golden-plan");
        planList.innerHTML = "";
        (data.golden_dataset_plan || []).forEach((item) => {
            const li = document.createElement("li");
            li.textContent = item;
            planList.appendChild(li);
        });

        // Prompt analysis (if piped)
        const paSection = document.getElementById("wo-prompt-analysis");
        if (data.prompt_analysis) {
            const pa = data.prompt_analysis;
            document.getElementById("wo-pa-score").innerText = `${pa.efficiency_score || "-"}%`;
            document.getElementById("wo-pa-prompt").value = pa.optimized_prompt || "";
            paSection.style.display = "block";
        } else {
            paSection.style.display = "none";
        }

        // Reveal
        container.style.display = "block";
        container.offsetHeight; // reflow
        container.style.transition = "opacity 0.5s ease";
        container.style.opacity = 1;
    }

    function resetOptimiserResults() {
        const container = document.getElementById("wo-results");
        container.style.display = "none";
        container.style.opacity = 0;
    }

    // ── Compounding Chart (Canvas) ──────────────────────────────────────────
    function renderCompoundingChart(chartData) {
        const canvas = document.getElementById("wo-chart");
        if (!canvas || !chartData.length) return;
        const ctx = canvas.getContext("2d");

        const rect = canvas.parentElement.getBoundingClientRect();
        canvas.width = rect.width;
        canvas.height = 180;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        const padding = { top: 20, right: 20, bottom: 30, left: 55 };
        const chartW = canvas.width - padding.left - padding.right;
        const chartH = canvas.height - padding.top - padding.bottom;

        const maxCumulative = Math.max(...chartData.map((d) => d.cumulative), 1);
        const barWidth = Math.max(20, Math.min(60, chartW / chartData.length - 8));
        const totalBarsWidth = chartData.length * (barWidth + 8);
        const startX = padding.left + (chartW - totalBarsWidth) / 2;

        // Y-axis labels
        ctx.fillStyle = "#9aa0a6";
        ctx.font = "10px Inter, sans-serif";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        for (let i = 0; i <= 4; i++) {
            const val = Math.round((maxCumulative / 4) * i);
            const y = padding.top + chartH - (chartH / 4) * i;
            ctx.fillText(val.toLocaleString(), padding.left - 8, y);
            // Grid line
            ctx.strokeStyle = "rgba(68, 71, 70, 0.4)";
            ctx.lineWidth = 0.5;
            ctx.beginPath();
            ctx.moveTo(padding.left, y);
            ctx.lineTo(canvas.width - padding.right, y);
            ctx.stroke();
        }

        chartData.forEach((d, i) => {
            const x = startX + i * (barWidth + 8);
            const baseH = (d.input_tokens / maxCumulative) * chartH;
            const histH = (d.history_tokens / maxCumulative) * chartH;
            const outH = (d.output_tokens / maxCumulative) * chartH;

            const bottomY = padding.top + chartH;

            // Input tokens (base) — blue
            ctx.fillStyle = "#4285f4";
            ctx.beginPath();
            ctx.roundRect(x, bottomY - baseH, barWidth, baseH, [0, 0, 4, 4]);
            ctx.fill();

            // History compounding — purple (stacked above)
            ctx.fillStyle = "#9b72cb";
            ctx.fillRect(x, bottomY - baseH - histH, barWidth, histH);

            // Output tokens — green (stacked above)
            ctx.fillStyle = "#00ff88";
            ctx.beginPath();
            ctx.roundRect(x, bottomY - baseH - histH - outH, barWidth, outH, [4, 4, 0, 0]);
            ctx.fill();

            // Cumulative label above bar
            ctx.fillStyle = "#e3e3e3";
            ctx.font = "9px Inter, sans-serif";
            ctx.textAlign = "center";
            ctx.fillText(
                d.cumulative.toLocaleString(),
                x + barWidth / 2,
                bottomY - baseH - histH - outH - 6
            );

            // X-axis label
            ctx.fillStyle = "#9aa0a6";
            ctx.font = "10px Inter, sans-serif";
            ctx.fillText(`Step ${d.step}`, x + barWidth / 2, bottomY + 16);
        });

        // Legend
        const legendY = 8;
        const legends = [
            { color: "#4285f4", label: "Input" },
            { color: "#9b72cb", label: "History" },
            { color: "#00ff88", label: "Output" },
        ];
        let legendX = canvas.width - padding.right;
        ctx.font = "9px Inter, sans-serif";
        ctx.textAlign = "right";
        for (let i = legends.length - 1; i >= 0; i--) {
            const leg = legends[i];
            const textW = ctx.measureText(leg.label).width;
            ctx.fillStyle = "#9aa0a6";
            ctx.fillText(leg.label, legendX, legendY + 6);
            legendX -= textW + 4;
            ctx.fillStyle = leg.color;
            ctx.fillRect(legendX - 8, legendY, 10, 10);
            legendX -= 20;
        }
    }

    // ── Resize handler ──────────────────────────────────────────────────────
    window.addEventListener("resize", () => {
        for (const [canvasId, instance] of Object.entries(canvasInstances)) {
            const canvas = document.getElementById(canvasId);
            if (canvas) {
                const rect = canvas.parentElement.getBoundingClientRect();
                canvas.width = rect.width;
                canvas.height = rect.height;
                drawGraph(canvasId, instance.trace, instance.currentVisibleNodes);
            }
        }
    });

    // ── Inline Trace Graph (retained from decision-graph.js) ────────────────
    function animateGraph(canvasId) {
        const instance = canvasInstances[canvasId];
        instance.currentVisibleNodes = 0;

        function nextStep() {
            const currentInstance = canvasInstances[canvasId];
            if (!currentInstance) return;

            if (currentInstance.currentVisibleNodes <= currentInstance.trace.length) {
                drawGraph(canvasId, currentInstance.trace, currentInstance.currentVisibleNodes);
                currentInstance.currentVisibleNodes++;
                if (currentInstance.currentVisibleNodes <= currentInstance.trace.length) {
                    setTimeout(nextStep, 600);
                }
            }
        }
        nextStep();
    }

    function drawGraph(canvasId, trace, maxNodesToShow) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;
        const ctx = canvas.getContext("2d");

        ctx.clearRect(0, 0, canvas.width, canvas.height);
        canvasInstances[canvasId].nodeBounds = [];

        if (!trace || trace.length === 0) return;

        const nodeWidth = 200;
        const nodeHeight = 50;
        const startX = canvas.width / 2 - nodeWidth / 2;
        let startY = 30;
        const ySpacing = 90;

        for (let i = 0; i < Math.min(trace.length, maxNodesToShow); i++) {
            const step = trace[i];

            // Connector line
            if (i > 0) {
                ctx.beginPath();
                ctx.moveTo(startX + nodeWidth / 2, startY - ySpacing + nodeHeight);
                ctx.lineTo(startX + nodeWidth / 2, startY);
                ctx.strokeStyle = "#4285f4";
                ctx.lineWidth = 2;
                ctx.setLineDash([5, 5]);
                ctx.stroke();
                ctx.setLineDash([]);

                if (step.tokens > 0) {
                    ctx.fillStyle = "#9aa0a6";
                    ctx.font = "10px Inter";
                    ctx.fillText(`${step.tokens} tokens`, startX + nodeWidth / 2 + 10, startY - ySpacing / 2);
                }
            }

            // Node box
            let bgColor = "#1e1f20";
            let borderColor = "#444746";

            if (step.step_type === "llm_call") borderColor = "#9b72cb";
            if (step.step_type === "tool_call") borderColor = "#f59e0b";
            if (step.step_type === "response") borderColor = "#00ff88";
            if (step.step_type === "escalation") borderColor = "#f97316";

            ctx.fillStyle = bgColor;
            ctx.beginPath();
            ctx.roundRect(startX, startY, nodeWidth, nodeHeight, 8);
            ctx.fill();

            ctx.strokeStyle = borderColor;
            ctx.lineWidth = 1;
            ctx.stroke();

            // Text
            ctx.fillStyle = "#e3e3e3";
            ctx.font = "12px Inter";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(step.description.substring(0, 30), startX + nodeWidth / 2, startY + nodeHeight / 2 - 6);

            ctx.fillStyle = "#9aa0a6";
            ctx.font = "10px Inter";
            ctx.fillText(`${step.latency_ms.toFixed(0)}ms`, startX + nodeWidth / 2, startY + nodeHeight / 2 + 10);

            canvasInstances[canvasId].nodeBounds.push({ x: startX, y: startY, w: nodeWidth, h: nodeHeight, step: step });
            startY += ySpacing;
        }
    }

    function setupCanvasInteractivity(canvas, canvasId) {
        const tooltip = document.getElementById("node-tooltip");

        canvas.addEventListener("mousemove", (e) => {
            const instance = canvasInstances[canvasId];
            if (!instance || instance.nodeBounds.length === 0) {
                tooltip.style.display = "none";
                canvas.style.cursor = "default";
                return;
            }
            const rect = canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;

            let hoveredNode = null;
            for (let b of instance.nodeBounds) {
                if (x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) {
                    hoveredNode = b;
                    break;
                }
            }

            if (hoveredNode) {
                canvas.style.cursor = "pointer";
                tooltip.style.display = "block";

                const tooltipWidth = 250;
                const xPos = e.clientX + 15 + tooltipWidth > window.innerWidth ? e.clientX - tooltipWidth - 15 : e.clientX + 15;

                tooltip.style.left = xPos + "px";
                tooltip.style.top = (e.clientY + 15) + "px";

                const step = hoveredNode.step;
                tooltip.innerHTML = `
                    <strong style="color: #4285f4">${step.step_type.toUpperCase()}</strong><br>
                    <span style="color: #fff">${step.description}</span><br><br>
                    Tokens: <span style="color: #9aa0a6">${step.tokens}</span><br>
                    Latency: <span style="color: #9aa0a6">${step.latency_ms.toFixed(0)}ms</span><br>
                    Model: <span style="color: #9aa0a6">${step.model_name || "N/A"}</span>
                `;
            } else {
                canvas.style.cursor = "default";
                tooltip.style.display = "none";
            }
        });

        canvas.addEventListener("mouseleave", () => {
            tooltip.style.display = "none";
            canvas.style.cursor = "default";
        });
    }
});
