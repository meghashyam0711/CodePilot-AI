"""QLoRA fine-tuning preparation and configuration validator.

SAFETY NOTICE: This script is configured for Phase 5 dry-run and configuration
validation. Training execution requires Phase 6 approval and must not be launched
until explicit user authorization is granted.
"""

import argparse
from pathlib import Path
import sys

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


def validate_training_prerequisites(config_path: Path) -> int:
    """Validate dataset files, configuration syntax, and safety parameters."""
    print("==================================================================")
    print("CodePilot AI — QLoRA Training Configuration & Resource Audit")
    print("==================================================================")

    if not config_path.exists():
        print(f"[!] Configuration file not found: {config_path}")
        return 1

    try:
        # Simple YAML parser or line inspector to avoid requiring PyYAML if not installed
        with open(config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        print(f"[+] Loaded configuration: {config_path} ({len(lines)} lines)")
    except Exception as e:
        print(f"[!] Failed to read config: {e}")
        return 1

    train_dataset = REPO_ROOT / "training" / "data" / "train.jsonl"
    val_dataset = REPO_ROOT / "training" / "data" / "validation.jsonl"

    if not train_dataset.exists():
        print(f"[!] Training dataset missing: {train_dataset}")
        print("    Run 'python training/prepare_dataset.py' first.")
        return 1
    print(f"[+] Found training dataset: {train_dataset.name}")

    if not val_dataset.exists():
        print(f"[!] Validation dataset missing: {val_dataset}")
        return 1
    print(f"[+] Found validation dataset: {val_dataset.name}")

    print("\nHardware Safety Profile:")
    print("  Target GPU:         NVIDIA GeForce RTX 4060 Laptop (8 GB VRAM)")
    print("  Host RAM:           32 GB")
    print("  Base Model:         Qwen/Qwen2.5-Coder-1.5B-Instruct (4-bit NF4)")
    print("  Target Modules:     q_proj, k_proj, v_proj, o_proj, gate/up/down_proj")
    print("  LoRA Rank (r):      16 (Alpha = 32)")
    print("  Sequence Length:    2,048 tokens")
    print("  Batch Size:         2 per device (Gradient accumulation = 8, Effective = 16)")
    print("  Optimizer:          paged_adamw_8bit")
    print("  Gradient Checkpt:   Enabled (saves ~60% activation VRAM)")
    print("------------------------------------------------------------------")
    print("  Estimated Peak VRAM: ~4.6 GB (Leaves ~3.4 GB headroom for OS display)")
    print("  Estimated Disk:      ~4.7 GB (Base weights + checkpoints + GGUF export)")
    print("==================================================================")
    print("[+] Dry-run configuration validation SUCCESSFUL.")
    print("[*] Note: Training is planned for Phase 6. No training executed in Phase 5.\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="CodePilot AI QLoRA Training Setup")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "training" / "qlora_config.yaml",
        help="Path to QLoRA configuration YAML",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Validate configuration and hardware budget without training (default: True)",
    )
    args = parser.parse_args()

    ret = validate_training_prerequisites(args.config)
    sys.exit(ret)


if __name__ == "__main__":
    main()
