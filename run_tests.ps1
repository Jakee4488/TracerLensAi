# run_tests.ps1

Write-Host "========================================="
Write-Host "  Starting GCP Agent Build & Test Script "
Write-Host "========================================="

Write-Host "`n[1] Activating Virtual Environment..."
if (Test-Path ".\venv\Scripts\Activate.ps1") {
    . ".\venv\Scripts\Activate.ps1"
    Write-Host "    -> Virtual environment activated."
} else {
    Write-Host "    -> ERROR: Virtual environment not found at .\venv. Please create it first."
    exit
}

Write-Host "`n[2] Setting Google Cloud Environment Variables..."
$env:GOOGLE_CLOUD_PROJECT="project-a566ac8f-a95c-4b33-88a"
$env:GOOGLE_CLOUD_REGION="us-central1"
Write-Host "    -> Project ID set to $env:GOOGLE_CLOUD_PROJECT"
Write-Host "    -> Region set to $env:GOOGLE_CLOUD_REGION"

Write-Host "`n[3] Setting API Keys (Add yours below if needed)..."
# Example of setting other API keys
# $env:OPENAI_API_KEY="sk-your-key"
# $env:ANTHROPIC_API_KEY="your-key"
Write-Host "    -> API keys configured."

Write-Host "`n[4] Running Unit Tests..."
if (Get-Command pytest -ErrorAction SilentlyContinue) {
    python -m pytest tests/
} else {
    Write-Host "    -> pytest not found. Skipping unit tests."
}

Write-Host "`n[5] Running the Synthetic Evaluation Harness..."
python -m src.observability.evaluator

Write-Host "`n========================================="
Write-Host "  Script Completed!"
Write-Host "========================================="
