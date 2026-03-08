"""
vision.py
---------
Uses Claude claude-3-5-sonnet (vision) to identify every recognisable product /
object in a set of uploaded images.

Why claude-3-5-sonnet?
  - State-of-the-art object & scene understanding
  - Handles cluttered rooms, partial occlusion, mixed lighting
  - Returns structured JSON we can feed straight into the reasoning step
"""

import base64
import json
import mimetypes
from pathlib import Path

import anthropic
from django.conf import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encode_image(image_path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type) for an image file."""
    mime, _ = mimetypes.guess_type(str(image_path))
    media_type = mime or "image/jpeg"
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


def _build_image_block(image_path: Path) -> dict:
    """Build an Anthropic image content block from a file path."""
    data, media_type = _encode_image(image_path)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

VISION_PROMPT = """
You are an expert product-recognition AI. Your job is to look at the provided
image(s) and identify every distinct physical product or object visible.

Focus on:
- Consumer products (appliances, electronics, clothing, furniture, packaging, food items, cleaning supplies, personal care items, etc.)
- Ignore purely structural elements like walls, floors, ceilings, windows.
- If you see the same type of object multiple times, only list it once.

Return ONLY a valid JSON array — no prose, no markdown fences.
Each element must be an object with these exact keys:
  "name"        : short product name (e.g. "plastic water bottle")
  "category"    : broad category (e.g. "drinkware", "electronics", "clothing")
  "description" : one sentence describing what you see (colour, brand if visible, condition)
  "likely_material": best guess at primary material (e.g. "single-use plastic", "polyester", "stainless steel")

Example output:
[
  {
    "name": "plastic water bottle",
    "category": "drinkware",
    "description": "A clear single-use PET plastic water bottle, branded 'Evian', approximately 500ml.",
    "likely_material": "single-use PET plastic"
  },
  {
    "name": "fast-fashion t-shirt",
    "category": "clothing",
    "description": "A pale-blue polyester blend t-shirt with a printed graphic.",
    "likely_material": "polyester blend"
  }
]

Return ONLY the JSON array.
""".strip()


def identify_objects(image_paths: list[Path]) -> list[dict]:
    """
    Send up to 10 images to Claude Vision and return a flat list of
    identified product objects.

    Args:
        image_paths: List of Path objects pointing to saved image files.

    Returns:
        List of dicts, each with keys: name, category, description, likely_material
    """
    if not image_paths:
        return []

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    # Build the content blocks: one image block per file, then the prompt
    content = []
    for path in image_paths[: settings.MAX_IMAGES_PER_REQUEST]:
        content.append(_build_image_block(path))

    content.append({"type": "text", "text": VISION_PROMPT})

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=2048,
        messages=[{"role": "user", "content": content}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if the model added them despite instructions
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        objects = json.loads(raw)
        if not isinstance(objects, list):
            objects = []
    except json.JSONDecodeError:
        objects = []

    return objects
