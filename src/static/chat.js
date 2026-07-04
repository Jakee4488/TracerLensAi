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

        // Append User Message
        appendMessage(text, "user");

        // Do not auto-open panel here as per user request

        // Show typing indicator or skeleton
        const loadingId = appendLoading();

        try {
            const response = await fetch("/inquire-traced", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ user_id: "demo_user", prompt: text })
            });

            const data = await response.json();
            
            // Remove loading
            document.getElementById(loadingId).remove();

            // Generate unique canvas for the decision trace graph
            const canvasId = "decision-graph-" + Date.now();
            let canvasHtml = `\n\n<div class="inline-graph-container" style="margin-top: 1rem; border-top: 1px solid #444746; padding-top: 1rem;">
                <canvas id="${canvasId}" class="inline-decision-graph" style="width: 100%; height: 250px;"></canvas>
            </div>`;
            
            // Append AI Message with Trace
            appendMessage(data.response + canvasHtml, "ai");

            // Dispatch event for the decision graph to pick up the trace
            window.dispatchEvent(new CustomEvent("newTraceData", {
                detail: {
                    prompt: text,
                    trace: data.trace,
                    metrics: data.metrics,
                    canvasId: canvasId
                }
            }));

        } catch (error) {
            document.getElementById(loadingId).remove();
            appendMessage("Sorry, I encountered an error. Please try again.", "ai");
            console.error(error);
        }
    }

    function appendMessage(text, sender) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `message ${sender}`;
        
        if (sender === "ai") {
            const htmlContent = typeof marked !== 'undefined' ? marked.parse(text) : escapeHtml(text).replace(/\n/g, '<br>');
            msgDiv.innerHTML = `
                <div class="avatar ai-avatar"></div>
                <div class="content markdown-body">${htmlContent}</div>
            `;
        } else {
            msgDiv.innerHTML = `<div class="content"><p>${escapeHtml(text).replace(/\n/g, '<br>')}</p></div>`;
        }
        
        messagesArea.appendChild(msgDiv);
        scrollToBottom();
    }

    function appendLoading() {
        const id = "loading-" + Date.now();
        const msgDiv = document.createElement("div");
        msgDiv.className = `message ai`;
        msgDiv.id = id;
        msgDiv.innerHTML = `
            <div class="avatar ai-avatar"></div>
            <div class="content">
                <div class="typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        `;
        messagesArea.appendChild(msgDiv);
        scrollToBottom();
        return id;
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

    // Sidebar Toggle
    document.getElementById("toggle-sidebar").addEventListener("click", () => {
        document.getElementById("sidebar").classList.toggle("collapsed");
    });

    // New Chat Action
    document.querySelector(".new-chat-btn").addEventListener("click", () => {
        // Clear chat area except welcome message
        messagesArea.innerHTML = `
            <div class="message ai">
                <div class="avatar ai-avatar"></div>
                <div class="content">
                    <p>Hi, I am TracerLensAi, your AI Observability and Workflow Optimization Engine. How can I help you today?</p>
                </div>
            </div>
        `;
        // Dispatch reset session event
        window.dispatchEvent(new CustomEvent("resetSession"));
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
