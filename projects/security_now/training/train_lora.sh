#!/bin/bash
# Train Security Now! LoRA adapter on Mac M1 Max
#
# Prerequisites:
#   pip install mlx-lm
#
# Usage:
#   ./train_lora.sh              # Use default config (Mistral 7B 4-bit, memory-optimized)
#   ./train_lora.sh config_small.yaml  # Use Llama 3.2 3B if still OOM
#
# Memory tips:
#   - Close browser and other apps if you get OOM errors
#   - config.yaml: Mistral 7B 4-bit, ~20GB RAM needed
#   - config_small.yaml: Llama 3.2 3B 4-bit, ~8GB RAM needed
#
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

# Show memory tip
echo "TIP: If you get OOM errors, close browser/apps or use config_small.yaml"
echo ""
echo "Starting LoRA training with config: $(basename $CONFIG)"
echo "This will take several hours on M1 Max."
echo ""

cd "$PROJECT_DIR"

# Run MLX-LM LoRA training using config file
python3 -m mlx_lm.lora --train --config "$CONFIG"

echo ""
echo "=== Training Complete ==="

# Determine adapter path from config
ADAPTER_PATH=$(grep "adapter_path:" "$CONFIG" | awk '{print $2}' | tr -d '"')
MODEL=$(grep "^model:" "$CONFIG" | awk '{print $2}' | tr -d '"')

echo "Adapter saved to: $PROJECT_DIR/$ADAPTER_PATH"
echo ""
echo "Next steps:"
echo "  1. Test the adapter:"
echo "     python3 -m mlx_lm.generate --model $MODEL --adapter-path $ADAPTER_PATH --prompt 'Explain TLS certificates'"
echo "  2. Export to GGUF: ./training/export_gguf.sh"
