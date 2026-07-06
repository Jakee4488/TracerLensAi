from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

app = FastAPI(title="TracerLensAi")

# Mount the static directory to serve index.html
app.mount("/static", StaticFiles(directory="src/static"), name="static")

@app.get("/")
def read_root():
    return RedirectResponse(url="/static/index.html")

@app.get("/health")
def health_check():
    return {"status": "ok"}

class PromptRequest(BaseModel):
    prompt: str
    causal_reasoning: bool = False

@app.post("/analyze-prompt")
def analyze_prompt(req: PromptRequest):
    if req.causal_reasoning:
        response_text = "I am a causal agent."
    else:
        response_text = "I am a normal agent."

    response_data = {
        "status": "success",
        "response": response_text
    }

    if req.causal_reasoning:
        response_data["causal_reasoning_steps"] = [
            "Step 1: Identifying potential confounding variables in the user's workflow.",
            "Step 2: Constructing Structural Causal Model (SCM) from trace history.",
            "Step 3: Estimating the Average Treatment Effect (ATE) for 'Provide user context' vs 'No context'.",
            "Step 4: Propensity score matching suggests a +12% lift in resolution rate.",
            "Conclusion: Causal inference supports executing the 'Fetch User Profile' tool before final response generation."
        ]

    return response_data
