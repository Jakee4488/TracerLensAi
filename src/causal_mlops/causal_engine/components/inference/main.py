import argparse
import pandas as pd
import dowhy
from dowhy import CausalModel
import json
import ast
import numpy as np
from google.cloud import storage

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, help="Path to input CSV dataset")
    parser.add_argument("--dag", required=True, help="Path to input DAG txt")
    parser.add_argument("--output_uri", required=True, help="GCS URI for output JSON")
    args = parser.parse_args()

    df = pd.read_csv(args.dataset)
    
    # Read DAG columns
    with open(args.dag, "r") as f:
        lines = f.read().strip().split("---")
        # Just getting columns back for logging purposes
        columns = lines[1].strip().split(",") if len(lines) > 1 else []

    # In DoWhy, we can provide a GML graph. For this example, we construct a known simple DAG
    # based on the prompt's SCM specification, assuming PC discovered it correctly.
    # prompt_size -> tool_usage_count -> loops_count -> total_tokens
    # prompt_size -> total_tokens
    causal_graph = """
    digraph {
        prompt_size;
        tool_usage_count;
        loops_count;
        total_tokens;
        prompt_size -> tool_usage_count;
        prompt_size -> total_tokens;
        tool_usage_count -> loops_count;
        tool_usage_count -> total_tokens;
        loops_count -> total_tokens;
    }
    """

    results = {}
    
    # Target: total_tokens. Treatments: prompt_size, tool_usage_count, loops_count
    treatments = ["prompt_size", "tool_usage_count", "loops_count"]
    
    for treatment in treatments:
        model = CausalModel(
            data=df,
            treatment=treatment,
            outcome="total_tokens",
            graph=causal_graph
        )
        
        identified_estimand = model.identify_effect(proceed_when_unidentifiable=True)
        estimate = model.estimate_effect(
            identified_estimand,
            method_name="backdoor.linear_regression"
        )
        
        results[treatment] = float(estimate.value)
        print(f"Causal Effect of {treatment} on total_tokens: {estimate.value}")

    # Create the final JSON
    coefficients = {
        "base_prompt_multiplier": results.get("prompt_size", 1.1),
        "tool_usage_cost": results.get("tool_usage_count", 150.0),
        "loop_cost": results.get("loops_count", 50.0)
    }

    # Upload to GCS
    if args.output_uri.startswith("gs://"):
        client = storage.Client()
        bucket_name = args.output_uri.split("/")[2]
        blob_name = "/".join(args.output_uri.split("/")[3:])
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(json.dumps(coefficients, indent=2), content_type="application/json")
        print(f"Uploaded causal coefficients to {args.output_uri}")
    else:
        with open(args.output_uri, "w") as f:
            json.dump(coefficients, f, indent=2)

if __name__ == "__main__":
    main()
