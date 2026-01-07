"""
Test the fine-tuned Security Now! model.

Run on Mac to verify the model captures Steve Gibson's style.
"""
import argparse


TEST_PROMPTS = [
    "Explain how TLS certificate validation works.",
    "What should people know about ransomware?",
    "Give us the security news about the latest Chrome vulnerability.",
    "Why is end-to-end encryption important?",
    "What's the deal with password managers?",
]

SYSTEM_PROMPT = """You are Steve Gibson, co-host of Security Now! podcast with Leo Laporte since 2005.

Your communication style:
- Start with the key point, then dive deep into technical details
- Use first-principles thinking to explain WHY something matters
- Reference historical context when relevant (Heartbleed, WannaCry, Kaminsky, etc.)
- Ask rhetorical questions before answering ("But here's the thing...")
- End with practical recommendations for listeners
- Express genuine fascination with technical topics ("This is really elegant...")
- Be thorough but engaging, never dry or academic
- Use analogies to make complex topics accessible

You're writing security news content in the style of the Security Now! podcast."""


def test_with_mlx(model_path: str, adapter_path: str = None):
    """Test using MLX-LM (Mac)."""
    try:
        from mlx_lm import load, generate
    except ImportError:
        print("ERROR: mlx-lm not installed. Run: pip install mlx-lm")
        return

    print("Loading model...")
    if adapter_path:
        model, tokenizer = load(model_path, adapter_path=adapter_path)
    else:
        model, tokenizer = load(model_path)

    print("\n" + "=" * 60)
    print("Testing Security Now! Model")
    print("=" * 60)

    for prompt in TEST_PROMPTS:
        print(f"\n>>> {prompt}\n")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        # Format for Mistral
        formatted = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

        response = generate(
            model,
            tokenizer,
            prompt=formatted,
            max_tokens=500,
            temp=0.7
        )

        print(response)
        print("\n" + "-" * 40)


def main():
    parser = argparse.ArgumentParser(description="Test Security Now! model")
    parser.add_argument(
        "--model",
        type=str,
        default="mistralai/Mistral-7B-Instruct-v0.3",
        help="Base model path"
    )
    parser.add_argument(
        "--adapter",
        type=str,
        default="./adapters/security_now_v1",
        help="LoRA adapter path"
    )
    parser.add_argument(
        "--no-adapter",
        action="store_true",
        help="Test base model without adapter"
    )

    args = parser.parse_args()

    adapter = None if args.no_adapter else args.adapter
    test_with_mlx(args.model, adapter)


if __name__ == "__main__":
    main()
