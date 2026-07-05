document.addEventListener("DOMContentLoaded", () => {
    const chatInput = document.getElementById("chat-input");
    const sendBtn = document.getElementById("send-btn");
    const messagesArea = document.getElementById("messages-area");
    
    // Configure marked to use highlight.js for code blocks
    if (typeof marked !== 'undefined') {
        marked.setOptions({
            highlight: function(code, lang) {
                if (lang && hljs.getLanguage(lang)) {
                    return hljs.highlight(code, { language: lang }).value;
                }
                return hljs.highlightAuto(code).value;
            }
        });
    }

    // Auto-resize textarea
    chatInput.addEventListener("input", function() {
        this.style.height = "auto";
        this.style.height = (this.scrollHeight) + "px";
    });

    // Send on enter (without shift)
    chatInput.addEventListener("keydown", function(e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    sendBtn.addEventListener("click", sendMessage);

    // Global state for canvas resizing
    const canvasInstances = {};

    async function sendMessage() {
        const text = chatInput.value.trim();
        if (!text) return;

        // Clear input
        chatInput.value = "";
        chatInput.style.height = "auto";

        const uniqueId = Date.now();
        const loadingId = `loading-${uniqueId}`;
        const resultsId = `results-${uniqueId}`;
        const chartCanvasId = `chart-${uniqueId}`;
        const graphCanvasId = `graph-${uniqueId}`;
        const textualAnalysisId = `textualAnalysis-${uniqueId}`;

        // Append Evaluation Card
        const cardHtml = `
        <div class="evaluation-card token-evaluation-card" style="background: #1e1f20; border: 1px solid #444; border-radius: 8px; margin-bottom: 1rem; padding: 1rem; color: #e3e3e3;">
            <h4 style="margin-top: 0; color: #fff;">Prompt: <span style="font-weight: normal; color: #9aa0a6;">${escapeHtml(text)}</span></h4>
            <div id="${loadingId}" class="typing-indicator" style="margin-top: 1rem;">
                <span></span><span></span><span></span>
            </div>
            
            <div id="${resultsId}" style="display: none; margin-top: 1rem;">
                <!-- Metrics Row -->
                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 1rem;">
                    <div class="metric-tile" style="background: #1a1a1a; padding: 1rem; border-radius: 8px; display: flex; flex-direction: column;">
                        <span style="font-size: 0.8rem; color: #9aa0a6;">Est. Total Tokens</span>
                        <strong id="estTotal-${uniqueId}" style="font-size: 1.5rem;">-</strong>
                    </div>
                    <div class="metric-tile" style="background: #1a1a1a; padding: 1rem; border-radius: 8px; display: flex; flex-direction: column;">
                        <span style="font-size: 0.8rem; color: #9aa0a6;">Cost Multiplier</span>
                        <strong id="costMult-${uniqueId}" style="font-size: 1.5rem; color: #f59e0b;">-</strong>
                    </div>
                    <div class="metric-tile" style="background: #1a1a1a; padding: 1rem; border-radius: 8px; display: flex; flex-direction: column;">
                        <span style="font-size: 0.8rem; color: #9aa0a6;">Efficiency Score</span>
                        <strong id="score-${uniqueId}" style="font-size: 1.5rem; color: #00ff88;">-</strong>
                    </div>
                    <div class="metric-tile" style="background: #1a1a1a; padding: 1rem; border-radius: 8px; display: flex; flex-direction: column;">
                        <span style="font-size: 0.8rem; color: #9aa0a6;">Risk Level</span>
                        <strong><span class="wo-risk-badge" id="risk-${uniqueId}" style="margin-top: 0.5rem; display: inline-block;">-</span></strong>
                    </div>
                </div>
                
                <div class="token-evaluation-body">
                    <div class="analysis-text-column">
                        <h5 style="margin-top: 0; margin-bottom: 0.75rem; color: #e3e3e3;">Token Analysis &amp; Compounding Report</h5>
                        <div id="${textualAnalysisId}" class="analysis-text-content">Analyzing trace steps...</div>
                    </div>

                    <div class="visual-stack">
                        <div class="visual-block">
                            <h5 style="margin-top: 0; margin-bottom: 0.5rem; color: #e3e3e3;">Causal Analysis Engine</h5>
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; font-size: 0.85rem;">
                                <div><span style="color: #9aa0a6;">Treatment</span><br><strong id="causalTreatment-${uniqueId}" style="color: #4285f4;">-</strong></div>
                                <div><span style="color: #9aa0a6;">Outcome</span><br><strong id="causalOutcome-${uniqueId}" style="color: #f59e0b;">-</strong></div>
                                <div style="grid-column: 1 / -1;"><span style="color: #9aa0a6;">Average Treatment Effect (ATE)</span><br><strong id="causalEffect-${uniqueId}" style="font-size: 1.1rem; color: #00ff88;">-</strong></div>
                                <div style="grid-column: 1 / -1; margin-top: 0.5rem;">
                                    <label style="color: #9aa0a6;">Recommendation</label>
                                    <textarea id="causalRec-${uniqueId}" readonly rows="2" style="width: 100%; background: #222; border: 1px solid #444; color: #fff; border-radius: 4px; padding: 0.5rem; font-size: 0.85rem; resize: none; margin-top: 0.2rem;"></textarea>
                                </div>
                            </div>
                        </div>

                        <div class="visual-block">
                            <h5 style="margin-top: 0; margin-bottom: 1rem; color: #e3e3e3;">Token Compounding per Step</h5>
                            <canvas id="${chartCanvasId}" style="width:100%; height:180px;"></canvas>
                        </div>

                        <div class="visual-block trace-graph-block" style="max-height: 250px; overflow-y: auto;">
                            <h5 style="margin-top: 0; margin-bottom: 1rem; color: #e3e3e3;">Trace Graph</h5>
                            <canvas id="${graphCanvasId}" style="width:100%;"></canvas>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        `;

        const tempDiv = document.createElement("div");
        tempDiv.innerHTML = cardHtml;
        messagesArea.appendChild(tempDiv.firstElementChild);
        scrollToBottom();

        try {
            // Fetch unified Analysis Report
            const analysisResponse = await fetch("/analyze-prompt", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ prompt: text })
            });
            const report = await analysisResponse.json();
            
            // Map new API structure back to what the UI expects
            const trace = report.synthetic_trace || [];
            
            // Reconstruct optData for charts/html rendering compatibility
            const optData = {
                prompt_analysis: {
                    efficiency_score: report.observability_metrics?.efficiency_ratio 
                        ? Math.round(report.observability_metrics.efficiency_ratio * 100) 
                        : 0,
                    optimization_tips: [report.causal_analysis?.recommendation || ""],
                    optimized_prompt: report.optimized_prompt
                },
                projection: {
                    estimated_total_tokens: report.token_prediction?.estimated_total_tokens || 0,
                    cost_multiplier: report.token_prediction?.cost_multiplier || 1.0,
                    risk_level: report.inference?.risk_level || "low",
                    token_velocity: Math.round(report.observability_metrics?.total_tokens_used / (report.observability_metrics?.total_latency_ms / 1000 || 1)) + " tokens/sec",
                    latency_bottleneck: report.observability_metrics?.bottleneck || "none"
                },
                compounding_chart: report.compounding_chart || []
            };

            const causalData = {
                treatment_name: report.causal_analysis?.treatment || "-",
                outcome_name: report.causal_analysis?.outcome || "-",
                estimated_effect: report.causal_analysis?.average_treatment_effect || 0,
                recommendation: report.causal_analysis?.recommendation || ""
            };

            // Hide loading, show results
            document.getElementById(loadingId).remove();
            document.getElementById(resultsId).style.display = "block";

            // Populate Metrics
            const proj = optData.projection || {};
            document.getElementById(`estTotal-${uniqueId}`).innerText = (proj.estimated_total_tokens || 0).toLocaleString();
            document.getElementById(`costMult-${uniqueId}`).innerText = `${proj.cost_multiplier || 0}×`;
            
            const pa = optData.prompt_analysis || {};
            document.getElementById(`score-${uniqueId}`).innerText = pa.efficiency_score ? `${pa.efficiency_score}%` : "N/A";
            
            const riskBadge = document.getElementById(`risk-${uniqueId}`);
            const risk = (proj.risk_level || "low").toLowerCase();
            riskBadge.innerText = risk.charAt(0).toUpperCase() + risk.slice(1);
            riskBadge.className = "wo-risk-badge risk-" + risk;

            // Populate Causal
            if (causalData) {
                document.getElementById(`causalTreatment-${uniqueId}`).innerText = causalData.treatment_name || "-";
                document.getElementById(`causalOutcome-${uniqueId}`).innerText = causalData.outcome_name || "-";
                document.getElementById(`causalEffect-${uniqueId}`).innerText = (causalData.estimated_effect || 0).toFixed(2);
                document.getElementById(`causalRec-${uniqueId}`).value = causalData.recommendation || "";
            }

            const textualAnalysis = document.getElementById(textualAnalysisId);
            if (textualAnalysis) {
                textualAnalysis.innerHTML = buildTokenAnalysisHtml(trace, optData);
            }

            // Render Graphs
            const chartCanvas = document.getElementById(chartCanvasId);
            if (chartCanvas) {
                const rect = chartCanvas.parentElement.getBoundingClientRect();
                chartCanvas.width = rect.width;
                chartCanvas.height = 180;
                renderCompoundingChart(chartCanvas, optData.compounding_chart || []);
            }

            const graphCanvas = document.getElementById(graphCanvasId);
            if (graphCanvas) {
                const rect = graphCanvas.parentElement.getBoundingClientRect();
                graphCanvas.width = rect.width;
                const ySpacing = 90;
                graphCanvas.height = Math.max(250, trace.length * ySpacing + 50);
                
                canvasInstances[graphCanvasId] = { trace: trace, currentVisibleNodes: 0 };
                animateGraph(graphCanvas, graphCanvasId);
            }

            scrollToBottom();

        } catch (error) {
            document.getElementById(loadingId).innerHTML = `<span style="color: #ff5252">Error calculating token usage. Try again.</span>`;
            console.error(error);
        }
    }

    function scrollToBottom() {
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    function escapeHtml(unsafe) {
        return unsafe
             .replace(/&/g, "&amp;")
             .replace(/</g, "&lt;")
             .replace(/>/g, "&gt;")
             .replace(/"/g, "&quot;")
             .replace(/'/g, "&#039;");
    }

    function buildTokenAnalysisHtml(trace, optData) {
        const steps = Array.isArray(trace) ? trace : [];
        const totalTokens = steps.reduce((sum, step) => sum + Number(step.tokens || 0), 0);
        const tokenBearingSteps = steps.filter((step) => Number(step.tokens || 0) > 0);
        const peakStep = tokenBearingSteps.reduce((best, step) => {
            if (!best) return step;
            return Number(step.tokens || 0) > Number(best.tokens || 0) ? step : best;
        }, null);

        const repromptSteps = steps.filter((step) => step.step_type === "re_prompt");
        const fallbackSteps = steps.filter((step) => step.token_fallback_used || step.used_fallback);
        const peakTransition = Array.isArray(optData?.dry_run_transitions)
            ? optData.dry_run_transitions.reduce((best, step) => {
                if (!best) return step;
                return Number(step.cumulative_context_tokens || 0) > Number(best.cumulative_context_tokens || 0) ? step : best;
            }, null)
            : null;

        const percentOfTotal = peakStep && totalTokens > 0
            ? Math.round((Number(peakStep.tokens || 0) / totalTokens) * 100)
            : 0;

        const parts = [];

        if (peakStep) {
            parts.push(`
                <p><strong>Largest token sink:</strong> ${escapeHtml(peakStep.description || peakStep.step_type)} used ${Number(peakStep.tokens || 0).toLocaleString()} tokens, or about ${percentOfTotal}% of the trace.</p>
            `);
        } else {
            parts.push(`
                <p><strong>Largest token sink:</strong> no token-bearing step was detected in this trace.</p>
            `);
        }

        if (repromptSteps.length > 0) {
            parts.push(`
                <p><strong>Compounding step:</strong> ${repromptSteps.length} re-prompt loop${repromptSteps.length === 1 ? "" : "s"} reintroduce prior tool output into the next model call, so each follow-up turn pays for both the new request and the accumulated history.</p>
            `);
        } else if (steps.some((step) => ["tool_call", "llm_call"].includes(step.step_type))) {
            parts.push(`
                <p><strong>Compounding pressure:</strong> repeated model and tool turns expand the prompt history even without an explicit re-prompt loop, so downstream calls still inherit prior context.</p>
            `);
        } else {
            parts.push(`
                <p><strong>Compounding step:</strong> no obvious loop was detected, so the trace stays mostly linear.</p>
            `);
        }

        if (peakTransition) {
            parts.push(`
                <p><strong>Highest projected accumulation:</strong> ${escapeHtml(peakTransition.description || peakTransition.step_type)} pushed the dry-run context window to ${Number(peakTransition.cumulative_context_tokens || 0).toLocaleString()} tokens.</p>
            `);
        }

        if (fallbackSteps.length > 0) {
            parts.push(`
                <p><strong>Token provenance:</strong> ${fallbackSteps.length} step${fallbackSteps.length === 1 ? "" : "s"} fell back to heuristic counting, so the figures below are simulated predictions rather than live gateway measurements.</p>
            `);
        } else {
            parts.push(`
                <p><strong>Token provenance:</strong> this trace included gateway or client-reported counts for the measured steps.</p>
            `);
        }

        parts.push(`
            <ul>
                <li>Token cost compounds whenever a re-prompt carries prior tool output into the next model turn.</li>
                <li>Tool calls become more expensive when their outputs are large enough to inflate the next context window.</li>
                <li>The chart and graph are dry-run simulations, so they are useful for comparison but not billing.</li>
            </ul>
        `);

        return parts.join("");
    }

    // ── Compounding Chart (Canvas) ──────────────────────────────────────────
    function renderCompoundingChart(canvas, chartData) {
        if (!chartData.length) return;
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        const padding = { top: 20, right: 20, bottom: 30, left: 45 };
        const chartW = canvas.width - padding.left - padding.right;
        const chartH = canvas.height - padding.top - padding.bottom;

        const maxCumulative = Math.max(...chartData.map((d) => d.cumulative), 1);
        const barWidth = Math.max(15, Math.min(40, chartW / chartData.length - 8));
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

            // X-axis label
            ctx.fillStyle = "#9aa0a6";
            ctx.font = "9px Inter, sans-serif";
            ctx.textAlign = "center";
            ctx.fillText(`Step ${d.step}`, x + barWidth / 2, bottomY + 16);
        });
    }

    // ── Inline Trace Graph ──────────────────────────────────────────────────
    function animateGraph(canvas, canvasId) {
        const instance = canvasInstances[canvasId];
        instance.currentVisibleNodes = 0;

        function nextStep() {
            const currentInstance = canvasInstances[canvasId];
            if (!currentInstance) return;

            if (currentInstance.currentVisibleNodes <= currentInstance.trace.length) {
                drawGraph(canvas, currentInstance.trace, currentInstance.currentVisibleNodes);
                currentInstance.currentVisibleNodes++;
                if (currentInstance.currentVisibleNodes <= currentInstance.trace.length) {
                    setTimeout(nextStep, 600);
                }
            }
        }
        nextStep();
    }

    function drawGraph(canvas, trace, maxNodesToShow) {
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, canvas.width, canvas.height);

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
                    ctx.fillText(`${step.tokens} tok`, startX + nodeWidth / 2 + 10, startY - ySpacing / 2);
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

            startY += ySpacing;
        }
    }

    // Sidebar Toggle
    document.getElementById("toggle-sidebar").addEventListener("click", () => {
        document.getElementById("sidebar").classList.toggle("collapsed");
    });

    // New Chat Action
    document.querySelector(".new-chat-btn").addEventListener("click", () => {
        messagesArea.innerHTML = `
            <div class="message ai">
                <div class="avatar ai-avatar"></div>
                <div class="content">
                    <p>Hi, I am TracerLensAi's Token Calculator. Enter a prompt below to evaluate its expected token usage across an agentic workflow trace.</p>
                </div>
            </div>
        `;
    });

    // Click on sample/recent workflows
    document.querySelectorAll(".history-item").forEach(item => {
        item.addEventListener("click", () => {
            let promptText = item.innerText;
            if (promptText === "Escalation test #42") {
                promptText = "I want to cancel my account, your service is terrible. I will sue.";
            } else if (promptText === "Order status inquiry") {
                promptText = "Hi, what is the status of my order 999?";
            } else if (promptText === "Recent workflows") {
                promptText = "Can you fetch my user profile?";
            }
            chatInput.value = promptText;
            sendMessage();
        });
    });
});
