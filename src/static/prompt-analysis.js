document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("prompt-analysis-form");
    const input = document.getElementById("prompt-analysis-input");
    const submitBtn = document.getElementById("prompt-analysis-submit");
    const status = document.getElementById("analysis-status");
    const results = document.getElementById("prompt-analysis-results");
    const score = document.getElementById("analysis-efficiency-score");
    const nodeCount = document.getElementById("analysis-node-count");
    const tipCount = document.getElementById("analysis-tip-count");
    const tipsList = document.getElementById("optimization-tips");
    const optimizedOutput = document.getElementById("optimized-prompt-output");
    const graph = document.getElementById("prompt-workflow-graph");

    if (!form) return;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const prompt = input.value.trim();
        if (!prompt) {
            setStatus("Add a prompt", "warn");
            return;
        }

        setStatus("Analyzing", "busy");
        submitBtn.disabled = true;

        try {
            const response = await fetch("/analyze-prompt", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ prompt })
            });

            if (!response.ok) {
                throw new Error(`Analysis failed with status ${response.status}`);
            }

            const data = await response.json();
            renderAnalysis(data);
            setStatus("Complete", "success");
        } catch (error) {
            console.error(error);
            setStatus("Failed", "error");
        } finally {
            submitBtn.disabled = false;
        }
    });

    function renderAnalysis(data) {
        const nodes = data.simulated_workflow_nodes || [];
        results.hidden = false;
        score.textContent = `${data.efficiency_score}%`;
        nodeCount.textContent = nodes.length;
        tipCount.textContent = (data.optimization_tips || []).length;
        optimizedOutput.value = data.optimized_prompt || "";

        tipsList.innerHTML = "";
        (data.optimization_tips || []).forEach((tip) => {
            const li = document.createElement("li");
            li.textContent = tip;
            tipsList.appendChild(li);
        });

        renderWorkflowGraph(nodes);
        window.dispatchEvent(new CustomEvent("promptAnalysisComplete", { detail: data }));
    }

    function renderWorkflowGraph(nodes) {
        graph.innerHTML = "";
        if (!nodes.length) return;

        const nodeWidth = 148;
        const nodeHeight = 82;
        const gap = 46;
        const margin = 24;
        const width = margin * 2 + nodes.length * nodeWidth + (nodes.length - 1) * gap;
        const height = 160;
        graph.setAttribute("viewBox", `0 0 ${width} ${height}`);
        graph.setAttribute("width", width);
        graph.setAttribute("height", height);

        const defs = svgElement("defs");
        const marker = svgElement("marker", {
            id: "analysis-arrow",
            viewBox: "0 0 10 10",
            refX: "8",
            refY: "5",
            markerWidth: "6",
            markerHeight: "6",
            orient: "auto-start-reverse"
        });
        marker.appendChild(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "graph-arrow-head" }));
        defs.appendChild(marker);
        graph.appendChild(defs);

        nodes.forEach((node, index) => {
            const x = margin + index * (nodeWidth + gap);
            const y = 34;

            if (index > 0) {
                graph.appendChild(svgElement("line", {
                    x1: x - gap + 8,
                    y1: y + nodeHeight / 2,
                    x2: x - 10,
                    y2: y + nodeHeight / 2,
                    class: "workflow-edge",
                    "marker-end": "url(#analysis-arrow)"
                }));
            }

            const group = svgElement("g", { class: "workflow-node" });
            group.appendChild(svgElement("rect", {
                x,
                y,
                width: nodeWidth,
                height: nodeHeight,
                rx: "8",
                class: `workflow-node-box stage-${node.stage}`
            }));

            addText(group, node.label, x + 14, y + 24, "workflow-node-title", 17);
            addText(group, `${node.tokens} tokens`, x + 14, y + 52, "workflow-node-meta", 17);
            addText(group, `${Math.round(node.latency_ms)} ms`, x + 14, y + 68, "workflow-node-meta", 17);
            graph.appendChild(group);
        });
    }

    function addText(parent, value, x, y, className, maxChars) {
        const lines = wrapText(value, maxChars);
        lines.forEach((line, index) => {
            const text = svgElement("text", {
                x,
                y: y + index * 14,
                class: className
            });
            text.textContent = line;
            parent.appendChild(text);
        });
    }

    function wrapText(value, maxChars) {
        const words = String(value).split(/\s+/);
        const lines = [];
        let current = "";
        words.forEach((word) => {
            const next = current ? `${current} ${word}` : word;
            if (next.length > maxChars && current) {
                lines.push(current);
                current = word;
            } else {
                current = next;
            }
        });
        if (current) lines.push(current);
        return lines.slice(0, 2);
    }

    function svgElement(tag, attributes = {}) {
        const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
        Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
        return element;
    }

    function setStatus(text, state) {
        status.textContent = text;
        status.dataset.state = state;
    }
});
