import os
import vertexai
from vertexai.preview import reasoning_engines

project_id = "icarus-agent-26"
location = "europe-west2"
vertexai.init(project=project_id, location=location, staging_bucket="gs://icarus-agent-26-agent-cache")

print("Deploying Reasoning Engine...")


# Load the agent App class
from src.agent import app

# Create reasoning engine
remote_app = reasoning_engines.ReasoningEngine.create(
    app,
    requirements="requirements.txt",
    display_name="TracerLensAi",
    extra_packages=["src"]
)

print(f"Deployed: {remote_app.resource_name}")
