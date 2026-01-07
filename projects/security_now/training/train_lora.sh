#!/bin/bash
# Train Security Now! LoRA adapter on Mac M1 Max
#
# Prerequisites:
#   pip install mlx-lm
#
# Usage:
#   ./train_lora.sh [config.yaml]
#
# This script is designed to run on the Mac M1 Max workstation.
# Trigger remotely from Linux:
#   ssh mac "cd ~/security_now && ./training/train_lora.sh"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="${1:-$SCRIPT_DIR/config.yaml}"

echo "=== Security Now! LoRA Training ==="
echo "Project dir: $PROJECT_DIR"
echo "Config: $CONFIG"
echo ""

# Check for training data
if [ ! -f "$PROJECT_DIR/training_data/train.jsonl" ]; then
    echo "ERROR: Training data not found at $PROJECT_DIR/training_data/train.jsonl"
    echo "Run the dataset builder first:"
    echo "  python -m scraper.dataset_builder ./transcripts ./training_data"
    exit 1
fi

# Count training examples
TRAIN_COUNT=$(wc -l < "$PROJECT_DIR/training_data/train.jsonl")
VALID_COUNT=$(wc -l < "$PROJECT_DIR/training_data/valid.jsonl")
echo "Training examples: $TRAIN_COUNT"
echo "Validation examples: $VALID_COUNT"
echo ""

# Check MLX-LM installation
if ! python3 -c "import mlx_lm" 2>/dev/null; then
    echo "ERROR: mlx-lm not installed. Run: pip install mlx-lm"
    exit 1
fi

echo "Starting LoRA training..."
echo "This will take several hours on M1 Max."
echo ""

cd "$PROJECT_DIR"

# Run MLX-LM LoRA training
python3 -m mlx_lm.lora \
    --train \
    --model "mistralai/Mistral-7B-Instruct-v0.3" \
    --data "./training_data" \
    --batch-size 4 \
    --num-layers 16 \
    --iters 2000 \
    --learning-rate 1e-5 \
    --adapter-path "./adapters/security_now_v1" \
    --save-every 500

echo ""
echo "=== Training Complete ==="
echo "Adapter saved to: $PROJECT_DIR/adapters/security_now_v1"
echo ""
echo "Next steps:"
echo "  1. Test the adapter: python3 -m mlx_lm.generate --model mistralai/Mistral-7B-Instruct-v0.3 --adapter-path ./adapters/security_now_v1"
echo "  2. Export to GGUF: ./training/export_gguf.sh"
