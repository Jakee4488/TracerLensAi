import functions_framework
import pandas as pd
import numpy as np
from google.cloud import bigquery
import os
import logging

logging.basicConfig(level=logging.INFO)

# Initialize BigQuery client
# Assumes the function runs with a Service Account that has BigQuery Data Editor roles.
client = bigquery.Client()
dataset_id = os.environ.get("BQ_DATASET", "tracerlens_observability")
table_id = os.environ.get("BQ_TABLE", "agent_execution_traces")
table_ref = f"{client.project}.{dataset_id}.{table_id}"

def generate_synthetic_traces(num_records: int = 10000) -> pd.DataFrame:
    """Generates synthetic execution traces for causal analysis."""
    np.random.seed(42)
    
    # Base prompt size (independent variable)
    prompt_size = np.random.normal(500, 100, num_records).astype(int)
    prompt_size = np.clip(prompt_size, 100, 2000)
    
    # Tool usage counts (partially influenced by prompt size)
    # Larger prompts might request more tool usage
    tool_usage = np.random.poisson(prompt_size / 200) + np.random.randint(0, 3, num_records)
    
    # Loops count (influenced by tool usage)
    # Each tool usage might cause 1-2 loops
    loops_count = tool_usage * np.random.uniform(1.0, 2.0, num_records).astype(int) + np.random.poisson(1, num_records)
    
    # Total Token Count (influenced by prompt size, tool usage, and loops)
    # SCM Equation: tokens = a*prompt_size + b*tool_usage + c*loops_count + noise
    tokens = (
        prompt_size * 1.1 +
        tool_usage * 150 +
        loops_count * 50 +
        np.random.normal(0, 50, num_records)
    ).astype(int)
    
    df = pd.DataFrame({
        "trace_id": [f"trace_{i}" for i in range(num_records)],
        "prompt_size": prompt_size,
        "tool_usage_count": tool_usage,
        "loops_count": loops_count,
        "total_tokens": tokens,
        "timestamp": pd.Timestamp.utcnow()
    })
    
    return df

@functions_framework.http
def ingest_synthetic_traces(request):
    """HTTP Cloud Function entry point."""
    logging.info(f"Generating synthetic traces...")
    df = generate_synthetic_traces(10000)
    
    logging.info(f"Streaming {len(df)} records to BigQuery: {table_ref}")
    
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND",
        schema=[
            bigquery.SchemaField("trace_id", "STRING"),
            bigquery.SchemaField("prompt_size", "INTEGER"),
            bigquery.SchemaField("tool_usage_count", "INTEGER"),
            bigquery.SchemaField("loops_count", "INTEGER"),
            bigquery.SchemaField("total_tokens", "INTEGER"),
            bigquery.SchemaField("timestamp", "TIMESTAMP"),
        ]
    )
    
    # Ingest dataframe into BQ
    job = client.load_table_from_dataframe(
        df, table_ref, job_config=job_config
    )
    job.result()  # Wait for the job to complete
    
    logging.info("Successfully ingested data to BigQuery.")
    return f"Successfully generated and ingested {len(df)} synthetic traces.", 200
