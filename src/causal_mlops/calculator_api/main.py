from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google.cloud import storage
import json
import os
import logging

app = FastAPI(title="TracerLens Causal Token Predictor")
logging.basicConfig(level=logging.INFO)

# Global dictionary to hold the causal coefficients
causal_weights = {
    "base_prompt_multiplier": 1.1,
    "tool_usage_cost": 150.0,
    "loop_cost": 50.0
}

def load_weights_from_gcs():
    """Loads the latest causal coefficients from GCS."""
    bucket_name = os.environ.get("GCS_BUCKET_NAME")
    blob_name = os.environ.get("GCS_BLOB_NAME", "causal_coefficients.json")
    
    if not bucket_name:
        logging.warning("GCS_BUCKET_NAME not set. Using default weights.")
        return
        
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        if blob.exists():
            data = blob.download_as_text()
            weights = json.loads(data)
            global causal_weights
            causal_weights.update(weights)
            logging.info(f"Successfully loaded causal weights: {causal_weights}")
        else:
            logging.warning(f"Blob {blob_name} does not exist in bucket {bucket_name}. Using defaults.")
    except Exception as e:
        logging.error(f"Error loading weights from GCS: {e}")

@app.on_event("startup")
async def startup_event():
    load_weights_from_gcs()

class PredictionRequest(BaseModel):
    prompt_size: int
    expected_tool_usage: int
    expected_loops: int

class PredictionResponse(BaseModel):
    predicted_tokens: int
    weights_used: dict

@app.post("/predict_tokens", response_model=PredictionResponse)
async def predict_tokens(request: PredictionRequest):
    """Predicts the total token usage based on the causal SCM."""
    try:
        # SCM Equation: tokens = a*prompt_size + b*tool_usage + c*loops_count
        predicted = (
            request.prompt_size * causal_weights["base_prompt_multiplier"] +
            request.expected_tool_usage * causal_weights["tool_usage_cost"] +
            request.expected_loops * causal_weights["loop_cost"]
        )
        
        return PredictionResponse(
            predicted_tokens=int(predicted),
            weights_used=causal_weights
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
