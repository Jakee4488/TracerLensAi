let sessionTotalTokens = 0;
let currentChatId = null;

// Generate a UUID v4 for session tracking (client-side)
function generateSessionId() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

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

    async function sendMessage() {
        const text = chatInput.value.trim();
        if (!text) return;

        // Clear input
        chatInput.value = "";
        chatInput.style.height = "auto";

        // Generate a session ID on first message
        if (!currentChatId) {
            currentChatId = generateSessionId();
        }

        const uniqueId = Date.now();
        const loadingId = `loading-${uniqueId}`;

        // Append User Message
        const userHtml = `
            <div class="message user">
                <p style="margin: 0;">${escapeHtml(text)}</p>
            </div>
        `;
        messagesArea.innerHTML += userHtml;
        scrollToBottom();

        // Append Loading Indicator
        const loadingHtml = `
            <div id="${loadingId}" class="message ai">
                <div class="avatar ai-avatar"></div>
                <div class="content">
                    <div class="typing-indicator" style="margin: 0.5rem 0;">
                        <span></span><span></span><span></span>
                    </div>
                </div>
            </div>
        `;
        messagesArea.innerHTML += loadingHtml;
        scrollToBottom();

        const causalToggle = document.getElementById("causal-toggle");
        const isCausalReasoningEnabled = causalToggle ? causalToggle.checked : false;

        const webSearchToggle = document.getElementById("web-search-toggle");
        const isWebSearchEnabled = webSearchToggle ? webSearchToggle.checked : false;

        const modelSelect = document.getElementById("model-select");
        const selectedModel = modelSelect ? modelSelect.value : "gemini-2.5-flash";

        try {
            const analysisResponse = await fetch("/analyze-prompt", {
                method: "POST",
                headers: { "Content-Type": "application/json", ...(await authHeaders()) },
                body: JSON.stringify({
                    prompt: text,
                    causal_reasoning: isCausalReasoningEnabled,
                    web_search: isWebSearchEnabled,
                    model_name: selectedModel,
                    chat_id: currentChatId
                })
            });
            
            const report = await analysisResponse.json();

            if (!analysisResponse.ok) {
                throw new Error(report.detail || "Unknown error occurred on the backend.");
            }

            // Update Token Tally
            if (report.total_token_count) {
                sessionTotalTokens += report.total_token_count;
                const badge = document.getElementById("token-tally-badge");
                if (badge) {
                    badge.innerText = `${sessionTotalTokens.toLocaleString()} tokens used`;
                }
            }

            document.getElementById(loadingId).remove();

            let parsedResponse = "";
            if (typeof marked !== 'undefined' && report.response) {
                parsedResponse = marked.parse(report.response);
            } else {
                parsedResponse = escapeHtml(report.response || "No response received.");
            }
            let aiContent = `<div style="margin: 0; font-size: 0.95rem; color: var(--text-primary);">${parsedResponse}</div>`;

            const causalSteps = report.causal_reasoning_steps || [];
            const causalGraph = report.causal_graph;
            const hasGraph = !!(causalGraph && causalGraph.nodes && causalGraph.nodes.length);
            let pendingGraphRender = null;
            if (causalSteps.length > 0 || hasGraph) {
                const phase = (report.causal_status && report.causal_status.phase) || "";
                const badge = phase
                    ? ` <span class="causal-phase-badge">${escapeHtml(phase.replace(/_/g, " "))}</span>`
                    : "";
                let panel = `
                <div style="margin-top: 1rem; padding: 1rem; background: var(--bg-input); border-radius: 8px; border-left: 4px solid #9b72cb;">
                    <h6 style="margin: 0 0 0.5rem 0; color: var(--text-primary); font-size: 0.9rem;">🤖 Causal Reasoning Steps:${badge}</h6>`;
                if (causalSteps.length > 0) {
                    panel += `
                    <ul style="margin: 0; padding-left: 1.5rem; font-size: 0.85rem; color: var(--text-secondary);">
                        ${causalSteps.map(step => `<li>${escapeHtml(step)}</li>`).join('')}
                    </ul>`;
                }
                if (hasGraph) {
                    const graphContainerId = `causal-graph-${uniqueId}`;
                    panel += `<div id="${graphContainerId}" class="causal-graph-container"></div>`;
                    pendingGraphRender = { id: graphContainerId, graph: causalGraph };
                }
                panel += `</div>`;
                aiContent += panel;
            }

            const aiMessageHtml = `
            <div class="message ai">
                <div class="avatar ai-avatar"></div>
                <div class="content">
                    ${aiContent}
                </div>
            </div>
            `;
            messagesArea.innerHTML += aiMessageHtml;
            scrollToBottom();

            if (pendingGraphRender) {
                renderCausalGraph(pendingGraphRender.id, pendingGraphRender.graph);
            }

            // Refresh the sidebar so a newly started conversation appears
            loadHistoryList();

        } catch (error) {
            console.error(error);
            const loader = document.getElementById(loadingId);
            if (loader) {
                loader.innerHTML = `
                    <div class="avatar ai-avatar"></div>
                    <div class="content" style="background: var(--bg-sidebar); border: 1px solid var(--border); padding: 0.8rem 1.2rem; border-radius: 18px 18px 18px 4px;">
                        <span style="color: #ff5252">Error: ${error.message}</span>
                    </div>
                `;
            }
        }
    }

    function scrollToBottom() {
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    // ── Auth + History ──────────────────────────────────────────────────────

    async function authHeaders() {
        if (!window.tracerAuth) return {};
        const token = await window.tracerAuth.getIdToken();
        return token ? { "Authorization": `Bearer ${token}` } : {};
    }

    const historyItems = document.getElementById("history-items");

    async function loadHistoryList() {
        const headers = await authHeaders();
        if (!headers.Authorization) return;
        try {
            const res = await fetch("/history", { headers });
            if (!res.ok) return;
            const data = await res.json();
            renderHistoryList(data.conversations || []);
        } catch (e) {
            console.error("Failed to load history:", e);
        }
    }

    function renderHistoryList(conversations) {
        if (!historyItems) return;
        historyItems.innerHTML = "";
        if (conversations.length === 0) {
            historyItems.innerHTML = `<div class="history-item" style="color: var(--text-secondary); font-size: 0.85rem; cursor: default;">No saved workflows yet</div>`;
            return;
        }
        conversations.forEach(conv => {
            const item = document.createElement("div");
            item.className = "history-item clickable-history";
            item.textContent = conv.title;
            item.title = conv.title;
            item.addEventListener("click", () => loadConversation(conv.chat_id));
            historyItems.appendChild(item);
        });
    }

    function resetHistorySidebar(message) {
        if (!historyItems) return;
        historyItems.innerHTML = `<div class="history-item" style="color: var(--text-secondary); font-size: 0.85rem; cursor: default;">${message}</div>`;
    }

    async function loadConversation(chatId) {
        const headers = await authHeaders();
        if (!headers.Authorization) return;
        try {
            const res = await fetch(`/history/${encodeURIComponent(chatId)}`, { headers });
            if (!res.ok) return;
            const conv = await res.json();

            currentChatId = conv.chat_id;
            sessionTotalTokens = conv.total_tokens || 0;
            const badge = document.getElementById("token-tally-badge");
            if (badge) {
                badge.innerText = `${sessionTotalTokens.toLocaleString()} tokens used`;
            }

            messagesArea.innerHTML = "";
            (conv.messages || []).forEach(msg => {
                if (msg.role === "user") {
                    messagesArea.innerHTML += `
                        <div class="message user">
                            <p style="margin: 0;">${escapeHtml(msg.content)}</p>
                        </div>`;
                } else {
                    const parsed = (typeof marked !== 'undefined' && msg.content)
                        ? marked.parse(msg.content)
                        : escapeHtml(msg.content || "");
                    messagesArea.innerHTML += `
                        <div class="message ai">
                            <div class="avatar ai-avatar"></div>
                            <div class="content">
                                <div style="margin: 0; font-size: 0.95rem; color: var(--text-primary);">${parsed}</div>
                            </div>
                        </div>`;
                }
            });
            scrollToBottom();
        } catch (e) {
            console.error("Failed to load conversation:", e);
        }
    }

    if (window.tracerAuth) {
        window.tracerAuth.onChange((user) => {
            if (user) {
                loadHistoryList();
            } else {
                resetHistorySidebar("Sign in to see your saved workflows");
            }
        });
    }

    // ── Causal graph rendering (Mermaid) ────────────────────────────────────

    // Node ids and labels come from LLM output: treat as untrusted. Ids are
    // reduced to [a-zA-Z0-9_]; labels to a small character whitelist.
    function sanitizeGraphId(value) {
        return "n_" + String(value || "").replace(/[^a-zA-Z0-9_]/g, "_").slice(0, 40);
    }

    function sanitizeGraphLabel(value) {
        const clean = String(value || "").replace(/[^a-zA-Z0-9 .,:%+()/-]/g, " ")
            .replace(/\s+/g, " ").trim().slice(0, 60);
        return clean || "node";
    }

    function buildMermaidFlowchart(graph) {
        const statusClass = {
            pending: "cPending", active: "cActive", done: "cDone",
            failed: "cFailed", invalidated: "cAffected", replanned: "cAffected"
        };
        const lines = ["flowchart TD"];
        (graph.nodes || []).forEach(node => {
            const cls = statusClass[node.status] || "cPending";
            lines.push(`    ${sanitizeGraphId(node.id)}["${sanitizeGraphLabel(node.label || node.id)}"]:::${cls}`);
        });

        const criticalPairs = new Set();
        const path = graph.critical_path || [];
        for (let i = 0; i + 1 < path.length; i++) {
            criticalPairs.add(`${path[i]} ${path[i + 1]}`);
        }
        const criticalEdgeIndexes = [];
        (graph.edges || []).forEach((edge, index) => {
            const dashed = edge.relation === "informs" || edge.relation === "constrains";
            const arrow = dashed ? "-.->" : "-->";
            const label = sanitizeGraphLabel(edge.relation || "causes");
            lines.push(`    ${sanitizeGraphId(edge.source)} ${arrow}|${label}| ${sanitizeGraphId(edge.target)}`);
            if (criticalPairs.has(`${edge.source} ${edge.target}`)) {
                criticalEdgeIndexes.push(index);
            }
        });

        lines.push("    classDef cPending fill:#3c4043,stroke:#9aa0a6,color:#e8eaed;");
        lines.push("    classDef cActive fill:#174ea6,stroke:#8ab4f8,color:#e8eaed;");
        lines.push("    classDef cDone fill:#0d652d,stroke:#81c995,color:#e8eaed;");
        lines.push("    classDef cFailed fill:#8c1d18,stroke:#f28b82,color:#ffffff;");
        lines.push("    classDef cAffected fill:#7a5900,stroke:#fdd663,color:#ffffff;");
        criticalEdgeIndexes.forEach(index => {
            lines.push(`    linkStyle ${index} stroke:#9b72cb,stroke-width:3px;`);
        });
        return lines.join("\n");
    }

    async function renderCausalGraph(containerId, graph) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const fallback = () => {
            container.innerHTML = `<pre style="font-size: 0.75rem; overflow-x: auto; margin: 0;">${escapeHtml(JSON.stringify(graph, null, 2))}</pre>`;
        };
        if (typeof mermaid === "undefined") { fallback(); return; }
        try {
            const definition = buildMermaidFlowchart(graph);
            const { svg } = await mermaid.render(`mmd-${containerId}`, definition);
            container.innerHTML = svg;
        } catch (error) {
            console.error("Causal graph render failed:", error);
            fallback();
        }
    }

    function escapeHtml(unsafe) {
        if (typeof unsafe !== 'string') return '';
        return unsafe
             .replace(/&/g, "&amp;")
             .replace(/</g, "&lt;")
             .replace(/>/g, "&gt;")
             .replace(/"/g, "&quot;")
             .replace(/'/g, "&#039;");
    }

    // Sidebar Toggle
    document.getElementById("toggle-sidebar").addEventListener("click", () => {
        document.getElementById("sidebar").classList.toggle("collapsed");
    });

    // New Chat Action
    document.querySelector(".new-chat-btn").addEventListener("click", () => {
        currentChatId = null;
        sessionTotalTokens = 0;
        const badge = document.getElementById("token-tally-badge");
        if (badge) {
            badge.innerText = `0 tokens used`;
        }

        messagesArea.innerHTML = `
            <div class="message ai">
                <div class="avatar ai-avatar"></div>
                <div class="content">
                    <p>Hi, I am TracerLensAi's Causal Agent. Enter a prompt below to evaluate its expected causal graph across an agentic workflow trace.</p>
                </div>
            </div>
        `;
    });

    // Dark Mode Toggle
    const darkModeToggle = document.getElementById("dark-mode-toggle");
    if (darkModeToggle) {
        darkModeToggle.addEventListener("change", (e) => {
            if (!e.target.checked) {
                document.body.classList.add("light-mode");
            } else {
                document.body.classList.remove("light-mode");
            }
        });
    }
});
