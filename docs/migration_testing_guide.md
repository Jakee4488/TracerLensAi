# 🚀 Migration Deployment & Testing Guide

This guide walks you through verifying the new **Gemini Enterprise Agent Platform** migration locally, deploying it via your updated CI/CD pipeline, and validating the final production functionality.

---

## 1. Local Testing & Verification

Before pushing to GitHub, you can verify that the new Cloud Run proxy and frontend work seamlessly.

### Step 1: Run the Automated Pipeline
Use your local automation script to ensure no syntax errors were introduced during the migration:
```bash
./run_tests.sh test
```
*This will run Flake8 linting, pytest, and a smoke test against the `/analyze-prompt` endpoint.*

### Step 2: Start the Hot-Reload Server
Start the local proxy:
```bash
./run_tests.sh --start
```
*The server will boot up at `http://localhost:8080`.*

### Step 3: Test the UI and Proxy Mock
Since you likely do not have an active Vertex AI Agent Runtime endpoint configured locally:
1. Open `http://localhost:8080` in your browser.
2. Type a message like: *"Hello agent!"* and hit send.
3. The UI should successfully display the **Agent Proxy mock response** we built, confirming that the frontend correctly parses the new proxy payload structure.

---

## 2. Deploying to GCP

Now it's time to ship the changes to production using your updated keyless Workload Identity Federation pipeline.

### Step 1: Commit and Push
You can use your commit guard script to safely commit the changes:
```bash
./run_tests.sh --commit "feat: migrate to Gemini Agent Platform and lightweight proxy"
```
Once committed, push the changes to your `main` branch.

### Step 2: Monitor GitHub Actions
Navigate to the **Actions** tab in your GitHub repository. You will see the `cd.yml` workflow trigger. Watch the steps execute:
1. **Deploy to Agent Platform**: The pipeline installs `google-agents-cli` and pushes `src/agent.py` to the Vertex AI Agent Registry.
2. **Build and Deploy**: The pipeline builds the FastAPI proxy Docker image and deploys it to Cloud Run.

### Step 3: Configure Cloud Run Environment Variables
Once deployed, log into the **Google Cloud Console** → **Cloud Run** → **tracerlensai-app**.
You must add two environment variables so the proxy knows where to route traffic:
- `AGENT_ENGINE_ENDPOINT`: The URL of your newly deployed Vertex AI Agent Engine.
- `AGENT_API_KEY`: A valid API key or token with permissions to invoke the agent.

---

## 3. Production Functionality Testing

Once deployed and configured, visit your public URL (`https://tracerlensai.com`) to run the final end-to-end functionality tests.

### Test 1: The Memory Bank
1. Send a message: *"My favorite color is neon green."*
2. Wait for the response.
3. Send a follow-up message: *"What is my favorite color?"*
4. **Expected Result:** The agent correctly answers "neon green" without the proxy needing to manually inject the history, proving the **Memory Bank** is functioning.

### Test 2: Native Code Execution
1. Send a prompt: *"Write and execute a Python script that calculates the 20th Fibonacci number, and tell me the result."*
2. **Expected Result:** The agent leverages the Vertex AI secure sandbox tool, executes the code, and returns `6765`.

### Test 3: Grounded Web Search
1. Turn the **Web Search** toggle **ON**.
2. Send a prompt: *"Who won the most recent Super Bowl?"*
3. **Expected Result:** The agent utilizes the Google Search MCP tool and returns the correct, up-to-date real-world answer.

### Test 4: Causal Reasoning Payload
1. Turn the **Causal Reasoning** toggle **ON**.
2. Send a prompt analyzing a dataset or scenario.
3. **Expected Result:** The UI correctly renders the purple "🤖 Causal Reasoning Steps" block beneath the main text, proving the frontend successfully unpacked the new proxy payload format.
