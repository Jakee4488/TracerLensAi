let sessionTotalTokens = 0;

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
        
        const modelSelect = document.getElementById("model-select");
        const selectedModel = modelSelect ? modelSelect.value : "gemini-1.5-flash-001";

        try {
            const analysisResponse = await fetch("/analyze-prompt", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ 
                    prompt: text,
                    causal_reasoning: isCausalReasoningEnabled,
                    model_name: selectedModel
                })
            });
            const report = await analysisResponse.json();
            
            // Update Token Tally
            if (report.total_token_count) {
                sessionTotalTokens += report.total_token_count;
                const badge = document.getElementById("token-tally-badge");
                if (badge) {
                    badge.innerText = `${sessionTotalTokens.toLocaleString()} tokens used`;
                }
            }

            document.getElementById(loadingId).remove();

            let aiContent = `<p style="margin: 0; font-size: 0.95rem; color: var(--text-primary);">${escapeHtml(report.response || "No response received.")}</p>`;

            if (report.causal_reasoning_steps) {
                aiContent += `
                <div style="margin-top: 1rem; padding: 1rem; background: var(--bg-input); border-radius: 8px; border-left: 4px solid #9b72cb;">
                    <h6 style="margin: 0 0 0.5rem 0; color: var(--text-primary); font-size: 0.9rem;">🤖 Causal Reasoning Steps:</h6>
                    <ul style="margin: 0; padding-left: 1.5rem; font-size: 0.85rem; color: var(--text-secondary);">
                        ${report.causal_reasoning_steps.map(step => `<li>${escapeHtml(step)}</li>`).join('')}
                    </ul>
                </div>`;
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

    function escapeHtml(unsafe) {
        return unsafe
             .replace(/&/g, "&amp;")
             .replace(/</g, "&lt;")
             .replace(/>/g, "&gt;")
             .replace(/"/g, "&quot;")
             .replace(/'/g, "&#039;");
    }

    // ── Legacy Graph Functions Removed ──────────────────────────────────────

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
                    <p>Hi, I am TracerLensAi's Causal Agent. Enter a prompt below to evaluate its expected causal graph across an agentic workflow trace.</p>
                </div>
            </div>
        `;
    });

    // Click on sample/recent workflows
    document.querySelectorAll(".clickable-history").forEach(item => {
        item.addEventListener("click", () => {
            let promptText = item.innerText;
            if (promptText.includes("Placeholder Chat 1")) {
                promptText = "I want to cancel my account, your service is terrible. I will sue.";
            } else if (promptText.includes("Placeholder Chat 2")) {
                promptText = "Hi, what is the status of my order 999?";
            }
            chatInput.value = promptText;
            sendMessage();
        });
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
