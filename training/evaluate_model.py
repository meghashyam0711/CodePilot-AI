"""Script to evaluate coding model performance against test datasets."""

import argparse
import json
from pathlib import Path
import sys

# Ensure repository root and src are on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from codepilot.core.evaluation.runner import evaluate_dataset
from codepilot.core.models.factory import create_model_client


def run_evaluation(
    dataset_path: Path,
    provider: str = "mock",
    model: str | None = None,
    output_report: Path | None = None,
    output_json: Path | None = None,
) -> int:
    """Run model evaluation and output results."""
    print(f"[*] Initializing model client (provider='{provider}', model={model or 'auto'})...")
    client = create_model_client(override_provider=provider, override_model=model)

    print(f"[*] Evaluating dataset from: {dataset_path}")
    report = evaluate_dataset(dataset_path, client)

    print("\n=======================================================")
    print(f"CodePilot AI — Evaluation Results ({report.model_id})")
    print("=======================================================")
    print(f"  Total Test Cases:       {report.total_cases}")
    print(f"  Instruction Following:  {report.instruction_following_rate * 100:.1f}%")
    print(f"  Syntax Validity:        {report.syntax_validity_rate * 100:.1f}%")
    print(f"  Diff Hygiene Score:     {report.diff_hygiene_average * 100:.1f}%")
    print(f"  Clarification Accuracy: {report.clarification_accuracy * 100:.1f}%")
    print(f"  Validation Test Pass:   {report.test_pass_rate * 100:.1f}%")
    print(f"  Average Latency:        {report.average_latency_seconds:.2f}s")
    print(f"  Inference Speed:        {report.tokens_per_second:.1f} tokens/s")
    print("=======================================================\n")

    if output_report:
        output_report.parent.mkdir(parents=True, exist_ok=True)
        with open(output_report, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        print(f"[+] Saved markdown report to: {output_report}")

    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"[+] Saved JSON report to: {output_json}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate CodePilot AI model performance")
    parser.add_argument(
        "--test-dataset",
        type=Path,
        default=REPO_ROOT / "training" / "data" / "test.jsonl",
        help="Path to evaluation dataset JSONL",
    )
    parser.add_argument(
        "--provider",
        choices=["mock", "ollama"],
        default="mock",
        help="Model provider to evaluate (default: mock, for safe offline evaluation)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model name or override (e.g., qwen2.5-coder:1.5b)",
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        default=REPO_ROOT / "training" / "baseline_report.md",
        help="Path to save markdown evaluation report",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Path to save JSON evaluation report",
    )
    args = parser.parse_args()

    ret = run_evaluation(
        dataset_path=args.test_dataset,
        provider=args.provider,
        model=args.model,
        output_report=args.output_report,
        output_json=args.output_json,
    )
    sys.exit(ret)


if __name__ == "__main__":
    main()
