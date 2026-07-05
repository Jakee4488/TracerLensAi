from kfp import dsl
from kfp import compiler

# Define the image that will be built and pushed to Artifact Registry
COMPONENT_IMAGE = "us-central1-docker.pkg.dev/{{PROJECT_ID}}/causal-mlops-repo/causal-components:latest"

@dsl.component(
    base_image=COMPONENT_IMAGE,
    packages_to_install=["google-cloud-bigquery", "pandas", "db-dtypes"]
)
def extract_data_op(project_id: str, dataset_id: str, table_id: str) -> dsl.Dataset:
    """Extracts the latest execution traces from BigQuery."""
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id)
    query = f"SELECT prompt_size, tool_usage_count, loops_count, total_tokens FROM `{project_id}.{dataset_id}.{table_id}` LIMIT 10000"

    df = client.query(query).to_dataframe()

    # Save output dataset
    # dsl.Dataset is a file path where we can write our data
    import tempfile
    path = tempfile.mktemp(suffix=".csv")
    df.to_csv(path, index=False)

    return path

@dsl.component(
    base_image=COMPONENT_IMAGE
)
def causal_discovery_op(dataset: dsl.Input[dsl.Dataset]) -> dsl.Artifact:
    """Performs Causal Discovery using the PC algorithm (causal-learn)."""
    import subprocess
    import sys
    import tempfile

    # Call the script that we packaged into the image
    output_dag_path = tempfile.mktemp(suffix=".txt")

    subprocess.run([
        sys.executable,
        "/app/discovery/main.py",
        "--dataset", dataset.path,
        "--output", output_dag_path
    ], check=True)

    return output_dag_path

@dsl.component(
    base_image=COMPONENT_IMAGE
)
def causal_inference_op(dataset: dsl.Input[dsl.Dataset], dag: dsl.Input[dsl.Artifact], output_json_uri: str):
    """Performs Causal Inference using DoWhy to calculate SEM coefficients."""
    import subprocess
    import sys

    subprocess.run([
        sys.executable,
        "/app/inference/main.py",
        "--dataset", dataset.path,
        "--dag", dag.path,
        "--output_uri", output_json_uri
    ], check=True)

@dsl.pipeline(
    name="tracerlens-causal-engine",
    description="Pipeline to discover and calculate causal token weights"
)
def causal_pipeline(
    project_id: str = "your-project-id",
    dataset_id: str = "tracerlens_observability",
    table_id: str = "agent_execution_traces",
    output_gcs_uri: str = "gs://your-artifacts-bucket/causal_coefficients.json"
):
    # Step A
    extract_task = extract_data_op(
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id
    )

    # Step B
    discovery_task = causal_discovery_op(
        dataset=extract_task.output
    )

    # Step C
    causal_inference_op(
        dataset=extract_task.output,
        dag=discovery_task.output,
        output_json_uri=output_gcs_uri
    )


if __name__ == "__main__":
    compiler.Compiler().compile(
        pipeline_func=causal_pipeline,
        package_path="causal_pipeline.json"
    )
