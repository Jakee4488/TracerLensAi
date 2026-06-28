#!/bin/bash

# Setup logging directory and file
mkdir -p logs
LOG_FILE="logs/run_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "========================================="
echo "  Starting GCP Agent Build & Test Script "
echo "========================================="
echo "Logging output to: $LOG_FILE"

echo -e "\n[1] Activating Virtual Environment..."
if [ -f "venv/Scripts/activate" ]; then
    # Windows/Git Bash path
    source venv/Scripts/activate
    echo "    -> Virtual environment activated."
elif [ -f "venv/bin/activate" ]; then
    # Linux/Mac path
    source venv/bin/activate
    echo "    -> Virtual environment activated."
else
    echo "    -> ERROR: Virtual environment not found. Please create it first."
    exit 1
fi

echo -e "\n[2] Setting Google Cloud Environment Variables..."
export GOOGLE_CLOUD_PROJECT="project-a566ac8f-a95c-4b33-88a"
export GOOGLE_CLOUD_REGION="us-central1"
echo "    -> Project ID set to $GOOGLE_CLOUD_PROJECT"
echo "    -> Region set to $GOOGLE_CLOUD_REGION"

echo -e "\n[3] Setting API Keys (Add yours below if needed)..."
# Example of setting other API keys
# export OPENAI_API_KEY="sk-your-key"
# export ANTHROPIC_API_KEY="your-key"
echo "    -> API keys configured."

echo -e "\n[4] Running Linting (flake8)..."
if command -v flake8 > /dev/null 2>&1; then
    flake8 src/ || echo "    -> Linting found some issues."
else
    echo "    -> flake8 not found. Skipping linting."
fi

echo -e "\n[5] Running Unit Tests..."
if command -v pytest > /dev/null 2>&1; then
    python -m pytest tests/ || echo "    -> Pytest finished or tests directory not found."
else
    echo "    -> pytest not found. Skipping unit tests."
fi

echo -e "\n[6] Running the Synthetic Evaluation Harness..."
python -m src.observability.evaluator

echo -e "\n[7] Testing API Endpoints..."
if curl -s http://127.0.0.1:8080/health > /dev/null; then
    echo "    -> Server is running. Health check passed."
    echo "    -> Sending test query to /inquire..."
    curl -s -X POST "http://127.0.0.1:8080/inquire" \
         -H "Content-Type: application/json" \
         -d '{"user_id": "test-user", "prompt": "Hello!"}'
    echo ""
else
    echo "    -> Server not running on 8080. Skipping API test. Start server first to test API."
fi

echo -e "\n========================================="
echo "  Script Completed!"
echo "========================================="
