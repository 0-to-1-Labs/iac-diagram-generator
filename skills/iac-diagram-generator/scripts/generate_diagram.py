#!/usr/bin/env python3
"""
Architecture Diagram Generator
Renders a PNG architecture diagram from a prompt using Google's
Nano Banana Pro (Gemini 3 Pro Image) model via the Gemini API.

Bundled with the iac-diagram-generator plugin so diagram generation is
self-contained (no separate image-generation skill required).

Usage:
    python generate_diagram.py "Your diagram prompt here"
    python generate_diagram.py --prompt-file prompt.txt
    python generate_diagram.py --prompt-file - <<'EOF'
    ...prompt...
    EOF
    python generate_diagram.py --fast --resolution 1K "Quick draft"

Dependencies are installed on first run into a private virtual environment
(see plugin_env.py). The GEMINI_API_KEY check runs before any install.
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import plugin_env

# Nano Banana Pro, stable ID, released 2026-05-28. The -preview ID shut down 2026-06-25.
DEFAULT_MODEL = "gemini-3-pro-image"
# Nano Banana 2 (Flash). Faster and cheaper; good for drafts and iteration.
FAST_MODEL = "gemini-3.1-flash-image"
LABELS = {DEFAULT_MODEL: "Nano Banana Pro", FAST_MODEL: "Nano Banana 2"}

ASPECT_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
RESOLUTIONS = ["1K", "2K", "4K"]
# Diagrams are landscape and text-heavy. 2K costs the same as 1K on the Pro model.
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_RESOLUTION = "2K"
OUTPUT_PREFIX = "iac_diagram_"


def validate_api_key():
    """Validate that the GEMINI_API_KEY environment variable is set."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable is not set.")
        print("\nTo fix this, set your API key:")
        print("  export GEMINI_API_KEY='your-api-key-here'")
        print("\nGet your API key at: https://aistudio.google.com/apikey")
        sys.exit(1)
    return api_key


def import_genai():
    """Import google-genai, or exit with instructions."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("ERROR: The 'google-genai' package is not importable in this interpreter.")
        print(f"  Interpreter: {sys.executable}")
        print(f"  Install it with: {sys.executable} -m pip install -r "
              f"{plugin_env.REQUIREMENTS}")
        sys.exit(1)
    return genai, types


def explain_error(error, model):
    """Print an API error with hints matched to the message."""
    print(f"ERROR: Failed to generate diagram: {error}")
    text = str(error).lower()
    if "api key" in text or "authentication" in text or "unauthenticated" in text:
        hints = [
            "Invalid API key",
            "API key not properly set in GEMINI_API_KEY environment variable",
            "API key may have been revoked or expired",
        ]
    elif "quota" in text or "rate limit" in text or "resource_exhausted" in text:
        hints = [
            "API quota exceeded",
            "Rate limit reached",
            "This model may have no free tier; check billing at https://aistudio.google.com/apikey",
            "Try again in a few moments",
        ]
    elif "not found" in text or "model" in text:
        hints = [
            f"The model '{model}' is not available to your API key/region",
            "Try the default Pro model, or '--fast' for the Flash model",
        ]
    elif "network" in text or "connection" in text:
        hints = [
            "Network connectivity issues",
            "Firewall blocking API requests",
            "Check your internet connection",
        ]
    else:
        hints = None
    if hints:
        print("\nPossible causes:")
        for hint in hints:
            print(f"  - {hint}")


def describe_empty_response(response):
    """Explain why no image came back (safety block, refusal, text only)."""
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None:
        reason = getattr(feedback, "block_reason", None)
        if reason:
            print(f"  Prompt feedback: blocked ({reason})")
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        finish = getattr(candidate, "finish_reason", None)
        if finish:
            print(f"  Finish reason: {finish}")
    print("  The model may have refused the request or returned text only.")


def generate_image(prompt, model, aspect_ratio, resolution):
    """
    Generate a diagram image with the Gemini image model.

    Returns:
        Image object or None if generation failed
    """
    api_key = validate_api_key()
    genai, types = import_genai()
    # The SDK logs an "automatic function calling" warning on every
    # generate_content call even when no tools are used; keep output clean.
    logging.getLogger("google_genai").setLevel(logging.ERROR)

    label = LABELS.get(model, model)
    print(f"Generating diagram with {label} ({model})...")
    print(f"Output: aspect_ratio={aspect_ratio}, resolution={resolution}")
    print(f"Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}\n")

    config = types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"],
        image_config=types.ImageConfig(
            aspect_ratio=aspect_ratio,
            image_size=resolution,
        ),
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=[prompt],
            config=config,
        )
    except Exception as e:
        explain_error(e, model)
        return None

    # `response.parts` is None when the request was blocked or returned no
    # candidates; never iterate it directly.
    parts = getattr(response, "parts", None) or []
    for part in parts:
        if part.text is not None:
            print(f"Model response: {part.text}")
        elif part.inline_data is not None:
            print("Diagram generated successfully!")
            return part.as_image()

    print("ERROR: No image data found in API response.")
    describe_empty_response(response)
    return None


def save_image(image, output_dir="."):
    """
    Save the generated diagram to a timestamped PNG file.

    Returns:
        Path to the saved file or None if save failed
    """
    try:
        directory = Path(output_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = directory / f"{OUTPUT_PREFIX}{timestamp}.png"
        counter = 1
        while filepath.exists():
            filepath = directory / f"{OUTPUT_PREFIX}{timestamp}_{counter}.png"
            counter += 1

        if hasattr(image, "save"):
            try:
                image.save(filepath, "PNG")
            except TypeError:
                # Gemini Image object takes only a filepath
                image.save(str(filepath))
        else:
            # Fallback for raw bytes
            with open(filepath, "wb") as f:
                f.write(image)
        print(f"\nDiagram saved to: {filepath.absolute()}")
        return filepath

    except Exception as e:
        print(f"ERROR: Failed to save diagram: {str(e)}")
        print("\nPossible causes:")
        print("  - Insufficient permissions to write to the directory")
        print("  - Disk space full")
        print("  - Invalid output directory path")
        return None


def read_prompt(args):
    """Return the prompt text from --prompt-file (or '-' for stdin) or argv."""
    if args.prompt_file:
        if args.prompt_file == "-":
            return sys.stdin.read()
        try:
            return Path(args.prompt_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"ERROR: Cannot read prompt file: {e}")
            sys.exit(1)
    return " ".join(args.prompt)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Render an architecture diagram with Nano Banana Pro (Gemini 3 Pro Image).",
    )
    parser.add_argument("prompt", nargs="*", help="The diagram prompt (or use --prompt-file).")
    parser.add_argument(
        "--prompt-file", metavar="PATH",
        help="Read the prompt from a file, or from stdin when PATH is '-'. "
             "Avoids shell expansion of $, backticks and quotes in long prompts.",
    )
    parser.add_argument(
        "--fast", action="store_true",
        help=f"Use the faster, cheaper Flash model ({FAST_MODEL}) instead of Pro.",
    )
    parser.add_argument(
        "--model", default=None,
        help=f"Explicit model ID (overrides --fast). Default: {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--aspect-ratio", choices=ASPECT_RATIOS, default=DEFAULT_ASPECT_RATIO,
        help=f"Output aspect ratio (default: {DEFAULT_ASPECT_RATIO}).",
    )
    parser.add_argument(
        "--resolution", choices=RESOLUTIONS, default=DEFAULT_RESOLUTION,
        help=f"Output resolution (default: {DEFAULT_RESOLUTION}).",
    )
    parser.add_argument(
        "--output-dir", default=".",
        help="Directory to save the PNG (default: current directory).",
    )
    parser.add_argument(
        "--data-dir", default=None, metavar="DIR",
        help="Plugin data directory for the managed Python environment "
             "(the skill passes ${CLAUDE_PLUGIN_DATA}).",
    )
    return parser.parse_args(argv)


def main():
    """Main entry point for the diagram generator."""
    args = parse_args()

    prompt = read_prompt(args).strip()
    if not prompt:
        print("ERROR: No prompt provided.")
        print('\nUsage: python generate_diagram.py "Your diagram prompt here"')
        print("       python generate_diagram.py --prompt-file prompt.txt")
        sys.exit(1)

    # Check the key before creating an environment or installing anything.
    validate_api_key()
    plugin_env.reexec_in_venv(args.data_dir)

    model = args.model or (FAST_MODEL if args.fast else DEFAULT_MODEL)

    image = generate_image(prompt, model, args.aspect_ratio, args.resolution)
    if image is None:
        sys.exit(1)

    filepath = save_image(image, args.output_dir)
    if filepath is None:
        sys.exit(1)

    print("\n✓ Diagram generation complete!")
    print(f"✓ Saved as: {filepath.name}")


if __name__ == "__main__":
    main()
