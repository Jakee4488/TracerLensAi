// TracerLensAi — Causal Agent UI
// Talks to the proxy backend: POST /analyze-prompt, POST /upload,
// GET /history, GET /history/{chat_id}. Auth rides on window.tracerAuth
// (defined by the Firebase module script in index.html).

let sessionTotalTokens = 0;
let currentChatId = null;

// Base URL for API calls. Empty = same-origin (default). Set
// window.TRACERLENS_API_BASE (in index.html) to the Cloud Run direct URL
// (e.g. https://api.tracerlensai.com) to bypass Firebase Hosting's 60s cap.
const API_BASE = (typeof window !== "undefined" && window.TRACERLENS_API_BASE) || "";

// Generate a UUID v4 for session tracking (client-side)
function generateSessionId() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

document.addEventListener("DOMContentLoaded", () => {
    const chatInput = document.getElementById("chat-input");
    const sendBtn = document.getElementById("send-btn");
    const messagesArea = document.getElementById("messages-area");
    const messagesInner = document.getElementById("messages-inner");
    const chipsRow = document.getElementById("attachment-chips");
    const attachBtn = document.getElementById("attach-btn");
    const fileInput = document.getElementById("file-input");
    const dropOverlay = document.getElementById("drop-overlay");
    const historyItems = document.getElementById("history-items");

    const GREETING = "Hi — I'm the TracerLensAi Causal Agent. Ask a question or attach data, "
        + "and I'll trace the cause-and-effect graph behind it.";

    // Mirrors ALLOWED_UPLOAD_EXTENSIONS / MAX_UPLOAD_BYTES in proxy/main.py.
    const ALLOWED_EXTENSIONS = [
        ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
        ".toml", ".xml", ".html", ".css", ".js", ".ts", ".py", ".java", ".go",
        ".rs", ".c", ".cpp", ".h", ".sh", ".sql", ".log",
    ];
    const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

    // {localId, id, name, size, status: "uploading" | "done" | "error"}
    const pendingAttachments = [];

    // ── Theme ───────────────────────────────────────────────────────────────

    const themeToggle = document.getElementById("theme-toggle");

    function applyTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        try { localStorage.setItem("tracerlens-theme", theme); } catch (e) { /* storage blocked */ }
        if (themeToggle) themeToggle.textContent = theme === "light" ? "☀" : "☾";
        if (typeof mermaid !== "undefined") {
            // Graphs rendered from now on pick up the new theme; existing SVGs
            // keep their explicit classDef colors (legible on both grounds).
            mermaid.initialize({
                startOnLoad: false,
                securityLevel: "strict",
                theme: theme === "light" ? "neutral" : "dark",
            });
        }
    }

    if (themeToggle) {
        themeToggle.textContent =
            document.documentElement.getAttribute("data-theme") === "light" ? "☀" : "☾";
        themeToggle.addEventListener("click", () => {
            const next = document.documentElement.getAttribute("data-theme") === "light"
                ? "dark" : "light";
            applyTheme(next);
        });
    }

    // ── Markdown pipeline (sanitized) ───────────────────────────────────────

    // Parse a fetch Response as JSON, tolerating non-JSON error bodies (e.g. an
    // HTML error page from a 502/504 gateway timeout) with a clear message
    // instead of a raw "Unexpected token '<'" SyntaxError.
    async function parseJsonResponse(response) {
        const raw = await response.text();
        try {
            return JSON.parse(raw);
        } catch (parseError) {
            if (!response.ok) {
                throw new Error(`Server error (${response.status}). Please try again in a moment.`);
            }
            throw new Error("The server returned an unexpected response.");
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

    function renderMarkdown(text) {
        if (typeof marked === "undefined") return escapeHtml(text || "");
        const html = marked.parse(text || "");
        // Never insert unsanitized model output; fall back to escaped text if
        // DOMPurify failed to load.
        if (typeof DOMPurify === "undefined") return escapeHtml(text || "");
        return DOMPurify.sanitize(html);
    }

    function highlightCode(container) {
        if (typeof hljs === "undefined") return;
        container.querySelectorAll("pre code").forEach((block) => {
            try { hljs.highlightElement(block); } catch (e) { /* leave plain */ }
        });
    }

    // ── DOM factories (append nodes; never innerHTML+= on the stream) ──────

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function scrollToBottom() {
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    function aiMessageShell() {
        const msg = el("div", "msg ai");
        const bubble = el("div", "bubble");
        msg.appendChild(el("div", "avatar"));
        msg.appendChild(bubble);
        return { msg, bubble };
    }

    function addGreeting() {
        const { msg, bubble } = aiMessageShell();
        bubble.appendChild(el("p", null, GREETING));
        messagesInner.appendChild(msg);
    }

    function addUserMessage(text, attachmentNames) {
        const msg = el("div", "msg user");
        const bubble = el("div", "bubble");
        bubble.appendChild(el("p", null, text));
        (attachmentNames || []).forEach((name) => {
            bubble.appendChild(el("span", "bubble-attachment", "⎘ " + name));
        });
        msg.appendChild(bubble);
        messagesInner.appendChild(msg);
        scrollToBottom();
        return msg;
    }

    function showTyping() {
        const { msg, bubble } = aiMessageShell();
        const typing = el("span", "typing");
        typing.setAttribute("aria-label", "Agent is thinking");
        for (let i = 0; i < 3; i++) typing.appendChild(el("i"));
        bubble.appendChild(typing);
        messagesInner.appendChild(msg);
        scrollToBottom();
        return msg;
    }

    function addErrorMessage(message) {
        const { msg, bubble } = aiMessageShell();
        bubble.classList.add("error");
        bubble.appendChild(el("p", null, "Error: " + message));
        messagesInner.appendChild(msg);
        scrollToBottom();
        return msg;
    }

    function addAiMessage(report) {
        const { msg, bubble } = aiMessageShell();
        const content = el("div", "md-content");
        content.innerHTML = renderMarkdown(report.response || "No response received.");
        bubble.appendChild(content);
        highlightCode(content);

        const steps = report.causal_reasoning_steps || [];
        const graph = report.causal_graph;
        const hasGraph = !!(graph && graph.nodes && graph.nodes.length);
        if (steps.length > 0 || hasGraph) {
            bubble.appendChild(buildCausalPanel(steps, graph, hasGraph, report.causal_status));
        }

        messagesInner.appendChild(msg);
        scrollToBottom();
        return msg;
    }

    function buildCausalPanel(steps, graph, hasGraph, status) {
        const panel = el("div", "causal-panel");
        const head = el("div", "causal-head", "⚯ Causal reasoning");
        const phase = status && status.phase;
        if (phase) {
            head.appendChild(el("span", "phase-badge", String(phase).replace(/_/g, " ")));
        }
        panel.appendChild(head);

        if (steps.length > 0) {
            const list = el("ul", "causal-steps");
            steps.forEach((step) => {
                const item = el("li");
                const match = /^\[([a-z_ -]+)\]\s*(.*)$/i.exec(String(step));
                if (match) {
                    const tagName = match[1].toLowerCase();
                    let tagClass = "step-tag";
                    if (tagName === "ok") tagClass += " ok";
                    if (tagName === "fail" || tagName === "failed") tagClass += " fail";
                    item.appendChild(el("span", tagClass, match[1]));
                    item.appendChild(document.createTextNode(match[2]));
                } else {
                    item.textContent = String(step);
                }
                list.appendChild(item);
            });
            panel.appendChild(list);
        }

        if (hasGraph) {
            const card = el("div", "graph-card");
            const container = el("div", "causal-graph-container");
            container.id = `causal-graph-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
            card.appendChild(container);

            const legend = el("div", "graph-legend");
            [["done", "done"], ["pending", "pending"], ["failed", "failed"], ["critical", "critical path"]]
                .forEach(([cls, label]) => {
                    const entry = el("span");
                    entry.appendChild(el("i", "lg-dot " + cls));
                    entry.appendChild(document.createTextNode(label));
                    legend.appendChild(entry);
                });
            card.appendChild(legend);
            panel.appendChild(card);

            // The panel is still detached here; mermaid.render works on an id
            // string and we mutate the container element directly, so the SVG
            // shows up once the message is appended.
            renderCausalGraph(container, graph);
        }
        return panel;
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
            criticalPairs.add(`${path[i]} ${path[i + 1]}`);
        }
        const criticalEdgeIndexes = [];
        (graph.edges || []).forEach((edge, index) => {
            const dashed = edge.relation === "informs" || edge.relation === "constrains";
            const arrow = dashed ? "-.->" : "-->";
            const label = sanitizeGraphLabel(edge.relation || "causes");
            lines.push(`    ${sanitizeGraphId(edge.source)} ${arrow}|${label}| ${sanitizeGraphId(edge.target)}`);
            if (criticalPairs.has(`${edge.source} ${edge.target}`)) {
                criticalEdgeIndexes.push(index);
            }
        });

        // Status colors from the neon-dark palette (legible in both themes).
        lines.push("    classDef cPending fill:#1b2030,stroke:#98a2b8,color:#e9edf7;");
        lines.push("    classDef cActive fill:#164e63,stroke:#22d3ee,color:#e9edf7;");
        lines.push("    classDef cDone fill:#064e3b,stroke:#34d399,color:#e9edf7;");
        lines.push("    classDef cFailed fill:#7f1d1d,stroke:#f87171,color:#ffffff;");
        lines.push("    classDef cAffected fill:#78350f,stroke:#fbbf24,color:#ffffff;");
        criticalEdgeIndexes.forEach(index => {
            lines.push(`    linkStyle ${index} stroke:#8b5cf6,stroke-width:3px;`);
        });
        return lines.join("\n");
    }

    async function renderCausalGraph(container, graph) {
        if (!container) return;
        const fallback = () => {
            const pre = el("pre", null, JSON.stringify(graph, null, 2));
            container.replaceChildren(pre);
        };
        if (typeof mermaid === "undefined") { fallback(); return; }
        try {
            const definition = buildMermaidFlowchart(graph);
            const { svg } = await mermaid.render(`mmd-${container.id}`, definition);
            // Mermaid output is generated locally from the whitelist-sanitized
            // definition above — safe to insert as-is.
            container.innerHTML = svg;
        } catch (error) {
            console.error("Causal graph render failed:", error);
            fallback();
        }
    }

    // ── Uploads ─────────────────────────────────────────────────────────────

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0) + " KB";
        return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    function chipNode(att) {
        const chip = el("span", "attach-chip");
        chip.dataset.attId = att.localId;
        chip.appendChild(el("span", "chip-name", att.name));
        chip.appendChild(el("span", "size", "· " + formatSize(att.size)));
        const remove = el("button", "chip-x", "✕");
        remove.type = "button";
        remove.setAttribute("aria-label", "Remove " + att.name);
        remove.addEventListener("click", () => removeAttachment(att.localId));
        chip.appendChild(remove);
        return chip;
    }

    function markChipError(att, chip, message) {
        att.status = "error";
        chip.classList.remove("uploading");
        chip.classList.add("error");
        chip.title = message;
    }

    function removeAttachment(localId) {
        const index = pendingAttachments.findIndex(a => a.localId === localId);
        if (index >= 0) pendingAttachments.splice(index, 1);
        const chip = chipsRow.querySelector(`[data-att-id="${localId}"]`);
        if (chip) chip.remove();
    }

    function clearAttachments() {
        pendingAttachments.length = 0;
        chipsRow.replaceChildren();
    }

    async function uploadFile(file, att, chip) {
        try {
            const form = new FormData();
            form.append("file", file);
            // Note: don't set Content-Type — the browser adds the boundary.
            const res = await fetch(`${API_BASE}/upload`, {
                method: "POST",
                headers: { ...(await authHeaders()) },
                body: form,
            });
            const data = await parseJsonResponse(res);
            if (!res.ok) throw new Error(data.detail || "Upload failed");
            att.id = data.file_id;
            att.status = "done";
            chip.classList.remove("uploading");
        } catch (error) {
            console.error("Upload failed:", error);
            markChipError(att, chip, error.message);
        }
    }

    function handleFiles(fileList) {
        Array.from(fileList || []).forEach((file) => {
            const dot = file.name.lastIndexOf(".");
            const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
            const att = {
                localId: "att-" + Date.now() + "-" + Math.random().toString(36).slice(2),
                id: null,
                name: file.name,
                size: file.size,
                status: "uploading",
            };
            pendingAttachments.push(att);
            const chip = chipNode(att);
            chip.classList.add("uploading");
            chipsRow.appendChild(chip);

            // Client-side pre-checks mirror the backend's 415/413 responses.
            if (!ALLOWED_EXTENSIONS.includes(ext)) {
                markChipError(att, chip, `Unsupported file type '${ext || file.name}'`);
                return;
            }
            if (file.size > MAX_UPLOAD_BYTES) {
                markChipError(att, chip, "File exceeds 5 MB limit");
                return;
            }
            uploadFile(file, att, chip);
        });
    }

    if (attachBtn && fileInput) {
        attachBtn.addEventListener("click", () => fileInput.click());
        fileInput.addEventListener("change", () => {
            handleFiles(fileInput.files);
            fileInput.value = "";
        });
    }

    // Drag & drop onto the whole page; depth counter avoids flicker on child
    // enter/leave events.
    let dragDepth = 0;

    function dragHasFiles(event) {
        const types = event.dataTransfer && event.dataTransfer.types;
        return !!types && Array.from(types).includes("Files");
    }

    document.addEventListener("dragenter", (event) => {
        if (!dragHasFiles(event)) return;
        event.preventDefault();
        dragDepth++;
        dropOverlay.classList.add("show");
    });
    document.addEventListener("dragover", (event) => {
        if (dragHasFiles(event)) event.preventDefault();
    });
    document.addEventListener("dragleave", (event) => {
        if (!dragHasFiles(event)) return;
        dragDepth = Math.max(0, dragDepth - 1);
        if (dragDepth === 0) dropOverlay.classList.remove("show");
    });
    document.addEventListener("drop", (event) => {
        if (!dragHasFiles(event)) return;
        event.preventDefault();
        dragDepth = 0;
        dropOverlay.classList.remove("show");
        handleFiles(event.dataTransfer.files);
    });

    // ── Send flow ───────────────────────────────────────────────────────────

    const tokenBadge = document.getElementById("token-tally-badge");

    function updateTokenBadge() {
        if (tokenBadge) {
            tokenBadge.textContent = `${sessionTotalTokens.toLocaleString()} tokens used`;
        }
    }

    // Auto-resize textarea
    chatInput.addEventListener("input", function () {
        this.style.height = "auto";
        this.style.height = (this.scrollHeight) + "px";
    });

    // Send on enter (without shift)
    chatInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    sendBtn.addEventListener("click", sendMessage);

    let isSending = false;

    async function sendMessage() {
        const text = chatInput.value.trim();
        if (!text || isSending) return;

        chatInput.value = "";
        chatInput.style.height = "auto";

        if (!currentChatId) {
            currentChatId = generateSessionId();
        }

        const readyAttachments = pendingAttachments.filter(a => a.status === "done");
        const attachmentIds = readyAttachments.map(a => a.id);
        const attachmentNames = readyAttachments.map(a => a.name);

        addUserMessage(text, attachmentNames);
        const typingMsg = showTyping();

        const causalToggle = document.getElementById("causal-toggle");
        const webSearchToggle = document.getElementById("web-search-toggle");
        const modelSelect = document.getElementById("model-select");

        isSending = true;
        sendBtn.disabled = true;
        try {
            const analysisResponse = await fetch(`${API_BASE}/analyze-prompt`, {
                method: "POST",
                headers: { "Content-Type": "application/json", ...(await authHeaders()) },
                body: JSON.stringify({
                    prompt: text,
                    causal_reasoning: causalToggle ? causalToggle.checked : false,
                    web_search: webSearchToggle ? webSearchToggle.checked : false,
                    model_name: modelSelect ? modelSelect.value : "gemini-2.5-flash",
                    chat_id: currentChatId,
                    attachments: attachmentIds,
                })
            });

            const report = await parseJsonResponse(analysisResponse);

            if (!analysisResponse.ok) {
                throw new Error(report.detail || "Unknown error occurred on the backend.");
            }

            if (report.total_token_count) {
                sessionTotalTokens += report.total_token_count;
                updateTokenBadge();
            }

            typingMsg.remove();
            addAiMessage(report);
            clearAttachments();

            // Refresh the sidebar so a newly started conversation appears
            loadHistoryList();
        } catch (error) {
            console.error(error);
            typingMsg.remove();
            addErrorMessage(error.message);
        } finally {
            isSending = false;
            sendBtn.disabled = false;
        }
    }

    // ── Auth + History ──────────────────────────────────────────────────────

    async function authHeaders() {
        if (!window.tracerAuth) return {};
        const token = await window.tracerAuth.getIdToken();
        return token ? { "Authorization": `Bearer ${token}` } : {};
    }

    async function loadHistoryList() {
        const headers = await authHeaders();
        if (!headers.Authorization) return;
        try {
            const res = await fetch(`${API_BASE}/history`, { headers });
            if (!res.ok) return;
            const data = await res.json();
            renderHistoryList(data.conversations || []);
        } catch (e) {
            console.error("Failed to load history:", e);
        }
    }

    function renderHistoryList(conversations) {
        if (!historyItems) return;
        historyItems.replaceChildren();
        if (conversations.length === 0) {
            historyItems.appendChild(el("div", "history-item hint", "No saved workflows yet"));
            return;
        }
        conversations.forEach(conv => {
            const item = el("div", "history-item", conv.title);
            item.title = conv.title;
            item.addEventListener("click", () => loadConversation(conv.chat_id));
            historyItems.appendChild(item);
        });
    }

    function resetHistorySidebar(message) {
        if (!historyItems) return;
        historyItems.replaceChildren(el("div", "history-item hint", message));
    }

    async function loadConversation(chatId) {
        const headers = await authHeaders();
        if (!headers.Authorization) return;
        try {
            const res = await fetch(`${API_BASE}/history/${encodeURIComponent(chatId)}`, { headers });
            if (!res.ok) return;
            const conv = await res.json();

            currentChatId = conv.chat_id;
            sessionTotalTokens = conv.total_tokens || 0;
            updateTokenBadge();

            messagesInner.replaceChildren();
            (conv.messages || []).forEach(msg => {
                if (msg.role === "user") {
                    addUserMessage(msg.content || "", msg.attachments || []);
                } else {
                    addAiMessage({ response: msg.content || "" });
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

    // ── Sidebar toggle & new chat ───────────────────────────────────────────

    document.getElementById("toggle-sidebar").addEventListener("click", () => {
        document.getElementById("sidebar").classList.toggle("collapsed");
    });

    document.getElementById("new-chat-btn").addEventListener("click", () => {
        currentChatId = null;
        sessionTotalTokens = 0;
        updateTokenBadge();
        clearAttachments();
        messagesInner.replaceChildren();
        addGreeting();
    });

    // Initial state
    addGreeting();
});
