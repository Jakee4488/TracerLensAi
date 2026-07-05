document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("prompt-analysis-form");
    const input = document.getElementById("prompt-analysis-input");
    const submitBtn = document.getElementById("prompt-analysis-submit");
    const results = document.getElementById("prompt-analysis-results");

    if (!form || !input || !submitBtn || !results) return;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const prompt = input.value.trim();
        if (!prompt) return;

        const previousLabel = submitBtn.innerText;
        submitBtn.innerText = "Optimising...";
        submitBtn.disabled = true;

        try {
            const response = await fetch("/analyze-prompt", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ prompt }),
            });
            if (!response.ok) {
                throw new Error(`Prompt analysis failed with status ${response.status}`);
            }

            const data = await response.json();
            document.getElementById("analysis-efficiency-score").innerText = `${data.efficiency_score || "-"}%`;
            document.getElementById("analysis-node-count").innerText = String((data.simulated_workflow_nodes || []).length);
            document.getElementById("analysis-tip-count").innerText = String((data.optimization_tips || []).length);

            const tipsList = document.getElementById("optimization-tips");
            tipsList.innerHTML = "";
            (data.optimization_tips || []).forEach((tip) => {
                const item = document.createElement("li");
                item.textContent = tip;
                tipsList.appendChild(item);
            });

            document.getElementById("optimized-prompt-output").value = data.optimized_prompt || "";
            results.hidden = false;
        } catch (error) {
            console.error(error);
        } finally {
            submitBtn.innerText = previousLabel;
            submitBtn.disabled = false;
        }
    });
});