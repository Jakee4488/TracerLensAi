import argparse
import pandas as pd
from causallearn.search.ConstraintBased.PC import pc

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, help="Path to input CSV dataset")
    parser.add_argument("--output", required=True, help="Path to output DAG text file")
    args = parser.parse_args()

    # Load dataset
    df = pd.read_csv(args.dataset)

    # Select columns for causal discovery
    # Order matters: prompt_size, tool_usage_count, loops_count, total_tokens
    columns = ["prompt_size", "tool_usage_count", "loops_count", "total_tokens"]
    data = df[columns].to_numpy()

    # Run PC algorithm
    print("Running PC algorithm for causal discovery...")
    cg = pc(data)

    # Save the output DAG to the text file
    print(f"Saving DAG to {args.output}")
    with open(args.output, "w") as f:
        # In a real scenario, we might export this as an adjacency matrix or GML
        # For simplicity, we output the matrix representation
        f.write(str(cg.G.graph))
        f.write("\n---\n")
        f.write(",".join(columns))


if __name__ == "__main__":
    main()
