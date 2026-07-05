document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("ai-agent-form");
    const input = document.getElementById("workflow-chat-input") || document.getElementById("ai-agent-input");
    const submitBtn = document.getElementById("workflow-send-btn") || document.getElementById("ai-agent-submit");
    const resultsContainer = document.getElementById("wo-results");
    const promptPanel = document.getElementById("prompt-analysis-panel");

    function setOptimiserView(showWorkflowOptimiser) {
        document.body.classList.toggle("workflow-mode", showWorkflowOptimiser);
        if (promptPanel) {
            promptPanel.hidden = showWorkflowOptimiser;
        }
        const viewLabel = showWorkflowOptimiser ? "Prompt Optimiser" : "Workflow Optimiser";
        if (submitBtn) {
            submitBtn.setAttribute("aria-label", viewLabel);
        }
    }

    async function sendWorkflowMessage() {
        const prompt = input.value.trim();
        if (!prompt) return;

        const originalText = submitBtn.innerText;
        submitBtn.innerText = "Simulating & Analysing...";
        submitBtn.disabled = true;

        try {
            const baseTraceResponse = await fetch("/inquire-traced", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ user_id: "workflow_user", prompt })
            });
            const baseTraceData = await baseTraceResponse.json();
            const baseCost = (baseTraceData.trace || []).reduce((acc, step) => acc + (step.tokens || 0), 0);

            const optPromptResponse = await fetch("/optimize-workflow", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    original_prompt: prompt,
                    trace: baseTraceData.trace,
                    run_prompt_analysis: true,
                }),
            });
            const optPromptDataRaw = await optPromptResponse.json();
            const optPromptData = optPromptDataRaw.prompt_analysis || {};
            const optimizedPrompt = optPromptData.optimized_prompt || prompt;

            const traceResponse = await fetch("/inquire-traced", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ user_id: "workflow_user", prompt: optimizedPrompt })
            });
            const traceData = await traceResponse.json();
            const trace = traceData.trace || [];

            const optResponse = await fetch("/optimize-workflow", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    original_prompt: optimizedPrompt,
                    trace: trace,
                    run_prompt_analysis: false,
                }),
            });
            const data = await optResponse.json();

            let causalData = null;
            try {
                const causalPayload = {
                    treatment: "includes_search_tool",
                    outcome: "total_token_cost",
                    traces: [
                        { prompt: "Calculate 2+2", total_token_cost: 100 },
                        { prompt: "search weather in London", total_token_cost: 300 },
                        { prompt: "search complex aggregate data", total_token_cost: 500 },
                        { prompt: optimizedPrompt, total_token_cost: data.projection?.estimated_total_tokens || 200 }
                    ]
                };
                const causalResp = await fetch("/causal-optimize", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(causalPayload),
                });
                if (causalResp.ok) {
                    causalData = await causalResp.json();
                }
            } catch (e) { console.warn(e); }

            renderOptimisation(data, causalData, baseCost, optPromptData);
        } catch (err) {
            console.error("Workflow optimisation failed", err);
            alert("Failed to optimise workflow. Check the console for details.");
        } finally {
            submitBtn.innerText = originalText;
            submitBtn.disabled = false;
        }
    }

    if (!form || !input || !submitBtn) return;

    setOptimiserView(false);
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        await sendWorkflowMessage();
    });

    function renderOptimisation(data, causalData, baseCost, optPromptData) {
        document.getElementById("ai-base-cost").innerText = baseCost.toLocaleString() + " tokens";
        document.getElementById("ai-opt-score").innerText = `${optPromptData.efficiency_score || "-"}%`;
        document.getElementById("ai-opt-prompt").value = optPromptData.optimized_prompt || "";
        document.getElementById("wo-formula").innerText = data.compounding_formula || "";

        const proj = data.projection || {};
        document.getElementById("wo-est-total").innerText = (proj.estimated_total_tokens || 0).toLocaleString();
        document.getElementById("wo-cost-mult").innerText = `${proj.cost_multiplier || 0}×`;
        document.getElementById("wo-comp-hist").innerText = (proj.compounding_history_tokens || 0).toLocaleString();

        const riskBadge = document.getElementById("wo-risk-badge");
        const risk = (proj.risk_level || "low").toLowerCase();
        riskBadge.innerText = risk.charAt(0).toUpperCase() + risk.slice(1);
        riskBadge.className = "wo-risk-badge risk-" + risk;

        if (causalData) {
            document.getElementById("wo-causal-treatment").innerText = causalData.treatment_name || "-";
            document.getElementById("wo-causal-outcome").innerText = causalData.outcome_name || "-";
            document.getElementById("wo-causal-effect").innerText = (causalData.estimated_effect || 0).toFixed(2);
            document.getElementById("wo-causal-rec").value = causalData.recommendation || "";
        } else {
            document.getElementById("wo-causal-treatment").innerText = "-";
            document.getElementById("wo-causal-outcome").innerText = "-";
            document.getElementById("wo-causal-effect").innerText = "-";
            document.getElementById("wo-causal-rec").value = "Analysis unavailable.";
        }

        const assumptions = data.assumptions || {};
        document.getElementById("wo-loops").innerText = assumptions.expected_loops || "-";
        document.getElementById("wo-base-p").innerText = assumptions.base_prompt_tokens || "-";
        document.getElementById("wo-avg-o").innerText = assumptions.average_model_output_tokens || "-";
        document.getElementById("wo-avg-t").innerText = assumptions.average_tool_output_tokens || "-";

        renderCompoundingChart(data.compounding_chart || []);
        resultsContainer.style.display = "block";
    }

    function renderCompoundingChart(chartData) {
        const canvas = document.getElementById("wo-chart");
        if (!canvas || !chartData.length) return;
        const ctx = canvas.getContext("2d");

        const rect = canvas.parentElement.getBoundingClientRect();
        canvas.width = rect.width;
        canvas.height = 160;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        const padding = { top: 20, right: 20, bottom: 30, left: 45 };
        const chartW = canvas.width - padding.left - padding.right;
        const chartH = canvas.height - padding.top - padding.bottom;

        const maxCumulative = Math.max(...chartData.map((d) => d.cumulative), 1);
        const barWidth = Math.max(20, Math.min(60, chartW / chartData.length - 8));
        const totalBarsWidth = chartData.length * (barWidth + 8);
        const startX = padding.left + (chartW - totalBarsWidth) / 2;

        ctx.fillStyle = "#9aa0a6";
        ctx.font = "10px Inter, sans-serif";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        for (let i = 0; i <= 4; i++) {
            const val = Math.round((maxCumulative / 4) * i);
            const y = padding.top + chartH - (chartH / 4) * i;
            ctx.fillText(val.toLocaleString(), padding.left - 8, y);
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

            ctx.fillStyle = "#4285f4";
            ctx.beginPath();
            ctx.roundRect(x, bottomY - baseH, barWidth, baseH, [0, 0, 4, 4]);
            ctx.fill();

            ctx.fillStyle = "#9b72cb";
            ctx.fillRect(x, bottomY - baseH - histH, barWidth, histH);

            ctx.fillStyle = "#00ff88";
            ctx.beginPath();
            ctx.roundRect(x, bottomY - baseH - histH - outH, barWidth, outH, [4, 4, 0, 0]);
            ctx.fill();

            ctx.fillStyle = "#9aa0a6";
            ctx.font = "10px Inter, sans-serif";
            ctx.textAlign = "center";
            ctx.fillText(`Step ${d.step}`, x + barWidth / 2, bottomY + 16);
        });
    }
});