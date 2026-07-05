from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.observability.logger import setup_logger
from src.analysis_engine.engines import AnalysisPipeline
from src.analysis_engine.models import AnalysisReport

app = FastAPI(title="TracerLensAi: Prompt Analysis & Optimization Platform")
logger = setup_logger()

# Mount static files
app.mount("/static", StaticFiles(directory="src/static"), name="static")

class AnalyzePromptRequest(BaseModel):
    prompt: str

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse("src/static/index.html")

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/analyze-prompt", response_model=AnalysisReport)
async def analyze_prompt_endpoint(request: AnalyzePromptRequest):
    if not request.prompt.strip():
        raise HTTPException(status_code=422, detail="Prompt cannot be empty")

    try:
        report = AnalysisPipeline.run(request.prompt)
        return report
    except Exception as e:
        logger.error(f"Error during analysis: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during analysis")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
