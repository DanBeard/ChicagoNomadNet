#!/bin/bash
# Export Security Now! fine-tuned model to GGUF format
#
# This fuses the LoRA adapter into the base model and converts to GGUF
# for use with llama.cpp on the Linux server.
#
# Prerequisites:
#   pip install mlx-lm
#   git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
#   pip install -r ~/llama.cpp/requirements.txt
#
# Usage:
#   ./export_gguf.sh [output_name] [quantization]
#
# Quantization options: f16, q8_0, q4_k_m (default), q4_0

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_NAME="${1:-security_now_mistral7b}"
QUANT="${2:-q4_k_m}"

LLAMA_CPP="${LLAMA_CPP_PATH:-$HOME/llama.cpp}"
# Use the same 4-bit model we trained on
BASE_MODEL="mlx-community/Mistral-7B-Instruct-v0.3-4bit"
ADAPTER_PATH="$PROJECT_DIR/adapters/security_now_v1"
FUSED_PATH="$PROJECT_DIR/fused_model"
OUTPUT_FILE="$PROJECT_DIR/${OUTPUT_NAME}.gguf"

echo "=== Security Now! GGUF Export ==="
echo "Base model: $BASE_MODEL"
echo "Adapter: $ADAPTER_PATH"
echo "Output: $OUTPUT_FILE"
echo "Quantization: $QUANT"
echo ""

# Check adapter exists
if [ ! -d "$ADAPTER_PATH" ]; then
    echo "ERROR: Adapter not found at $ADAPTER_PATH"
    echo "Run training first: ./training/train_lora.sh"
    exit 1
fi

# Check llama.cpp
if [ ! -d "$LLAMA_CPP" ]; then
    echo "ERROR: llama.cpp not found at $LLAMA_CPP"
    echo "Clone it: git clone https://github.com/ggml-org/llama.cpp $LLAMA_CPP"
    exit 1
fi

echo "Step 1: Fusing adapter into base model..."
python3 -m mlx_lm.fuse \
    --model "$BASE_MODEL" \
    --adapter-path "$ADAPTER_PATH" \
    --save-path "$FUSED_PATH"

echo ""
echo "Step 2: Converting to GGUF format..."
python3 "$LLAMA_CPP/convert_hf_to_gguf.py" "$FUSED_PATH" \
    --outfile "$OUTPUT_FILE" \
    --outtype "$QUANT"

echo ""
echo "=== Export Complete ==="
echo "GGUF file: $OUTPUT_FILE"
echo "Size: $(du -h "$OUTPUT_FILE" | cut -f1)"
echo ""
echo "Transfer to Linux server:"
echo "  scp $OUTPUT_FILE linux:/models/"
echo ""
echo "Run on Linux with llama.cpp:"
echo "  ./llama-server -m /models/${OUTPUT_NAME}.gguf --host 127.0.0.1 --port 8080"

# Optional: clean up fused model to save disk space
read -p "Delete fused model to save disk space? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    rm -rf "$FUSED_PATH"
    echo "Fused model deleted."
fi
