FROM python:3.10-slim

WORKDIR /app

# Install system dependencies if required by causal-learn/dowhy (e.g., graphviz)
RUN apt-get update && apt-get install -y graphviz && rm -rf /var/lib/apt/lists/*

# Install python dependencies
RUN pip install --no-cache-dir \
    pandas==2.0.3 \
    numpy==1.24.3 \
    causal-learn==0.1.3.8 \
    dowhy==0.11.1 \
    google-cloud-storage==2.10.0 \
    google-cloud-bigquery==3.11.4 \
    db-dtypes==1.1.1 \
    networkx==3.1

# Copy scripts
COPY discovery/ /app/discovery/
COPY inference/ /app/inference/

ENV PYTHONPATH="/app:${PYTHONPATH:-}"
