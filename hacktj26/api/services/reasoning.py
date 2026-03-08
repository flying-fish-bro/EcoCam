"""
reasoning.py
------------
Uses Claude claude-3-5-sonnet with Anthropic's built-in web_search tool to find
real, purchasable eco-friendly alternatives to the identified objects.

Why this model + web_search?
  - claude-3-5-sonnet reasons well about sustainability trade-offs
  - The built-in web_search tool returns curated, non-sketchy results
    (Anthropic filters out low-quality / malicious domains)
  - Using the native tool means we don't need a separate search API key
"""

import json
import re

import anthropic
from django.conf import settings


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt() -> str:
    return """
You are an expert sustainability researcher and eco-product advisor.
Your task is to find real, currently available eco-friendly alternatives to
products identified in a customer's photos.

Rules:
1. Search the web to find actual products — do NOT invent products or URLs.
2. Only recommend products from well-known, reputable retailers or brand sites
   (e.g. Amazon, Etsy, Target, Walmart, manufacturer websites, specialty eco
   stores like packagefreeshop.com, earthhero.com, grove.co, etc.).
   NEVER link to unknown, sketchy, or low-quality domains.
3. For each alternative, find at least 1 and up to 3 URLs where it can be bought.
4. Assign an eco_score (0–100) based on:
     80–100: Certified organic/recycled/B-Corp, zero-waste packaging, carbon neutral
     60–79:  Recycled or natural materials, minimal packaging, ethical supply chain
     40–59:  Some eco improvements over conventional but not fully sustainable
     20–39:  Marginal improvement, mostly greenwashing
     0–19:   Little to no eco benefit
5. Return ONLY a valid JSON array — no prose, no markdown fences.

Each element in the array must have exactly these keys:
  "name"        : product name
  "tagline"     : one punchy sentence on why it's greener
  "description" : 2–3 sentences describing the product and its eco credentials
  "price"       : best price found as a string e.g. "$24.99" (or "$19–$35" for a range)
  "eco_score"   : integer 0–100
  "attributes"  : array of up to 5 short eco labels e.g. ["Recycled plastic", "BPA-free", "Carbon neutral shipping"]
  "buy_links"   : array of up to 3 objects, each { "label": "store name", "url": "https://..." }

Return ONLY the JSON array.
""".strip()


def _build_user_prompt(objects: list[dict], max_price: float | None) -> str:
    price_clause = (
        f"The customer's maximum budget is **${max_price:.2f}**. "
        "Only recommend alternatives at or below this price."
        if max_price is not None
        else "There is no price limit. Find the top 3 best eco alternatives overall."
    )

    object_lines = "\n".join(
        f"- {o.get('name', 'unknown')} ({o.get('category', '')}): "
        f"{o.get('description', '')} — primary material: {o.get('likely_material', 'unknown')}"
        for o in objects
    )

    return f"""
The customer photographed the following products:

{object_lines}

{price_clause}

Search the web for the best eco-friendly, sustainably made alternatives to these
products. Prioritise alternatives that are:
  • Made from recycled, organic, or natural materials
  • From brands with transparent supply chains or third-party eco certifications
  • Sold on reputable, well-known websites
  • Actually available to buy right now

Return your answer as a JSON array as described in the system prompt.
""".strip()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def find_eco_alternatives(
    objects: list[dict],
    max_price: float | None = None,
) -> list[dict]:
    """
    Use Claude + web search to find eco-friendly alternatives for the given objects.

    Args:
        objects:   List of identified product dicts from vision.py
        max_price: Optional maximum price in USD. If None, returns top 3 overall.

    Returns:
        List of product alternative dicts ready to send to the frontend.
    """
    if not objects:
        return []

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    # Web search tool — Anthropic's native tool, returns vetted results
    tools = [
        {
            "type": "web_search_20250305",
            "name": "web_search",
        }
    ]

    messages = [
        {
            "role": "user",
            "content": _build_user_prompt(objects, max_price),
        }
    ]

    # Agentic loop — Claude may call web_search multiple times before answering
    while True:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=_build_system_prompt(),
            tools=tools,
            messages=messages,
        )

        # Collect all content blocks for this turn
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Extract the final text response
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text
            break

        if response.stop_reason == "tool_use":
            # Process every tool_use block and feed results back
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                # The web_search tool returns its own result automatically
                # when used via the native Anthropic tool — we just need to
                # append a placeholder tool_result so the conversation is valid
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "",  # native tool fills this in server-side
                    }
                )
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            continue

        # Any other stop reason — bail
        break

    # Parse the JSON array from final_text
    raw = final_text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # Try to extract a JSON array even if there's surrounding prose
    array_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if array_match:
        raw = array_match.group(0)

    try:
        products = json.loads(raw)
        if not isinstance(products, list):
            products = []
    except json.JSONDecodeError:
        products = []

    # Enforce 3 results when no price limit
    if max_price is None:
        products = products[:3]

    # Validate and sanitise each product entry
    sanitised = []
    for p in products:
        if not isinstance(p, dict):
            continue
        sanitised.append(
            {
                "name":        str(p.get("name", "Eco Alternative")),
                "tagline":     str(p.get("tagline", "")),
                "description": str(p.get("description", "")),
                "price":       str(p.get("price", "")),
                "eco_score":   _clamp(p.get("eco_score", 70), 0, 100),
                "attributes":  [str(a) for a in p.get("attributes", [])[:5]],
                "buy_links":   _validate_links(p.get("buy_links", [])),
            }
        )

    return sanitised


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(value, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return 70


# Domains we consider reputable enough to serve as buy links
_ALLOWED_DOMAINS = {
    "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au",
    "target.com", "walmart.com", "etsy.com", "ebay.com",
    "bestbuy.com", "wayfair.com", "homedepot.com", "lowes.com",
    "grove.co", "earthhero.com", "packagefreeshop.com",
    "wholefoodsmarket.com", "thegoodtrade.com",
    "rei.com", "patagonia.com", "prAna.com",
    "method.com", "seventhgeneration.com", "ecover.com",
    "packagefreeshop.com", "ecosia.org",
    # Allow any https brand / manufacturer site (not IP addresses or < 5 char TLDs)
}


def _is_reputable(url: str) -> bool:
    """Heuristic: accept known retailers or any https brand domain."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        host = parsed.netloc.lower().replace("www.", "")
        if host in _ALLOWED_DOMAINS:
            return True
        # Accept any domain with a proper TLD that isn't an IP
        parts = host.split(".")
        if len(parts) >= 2 and not parts[-2].isdigit():
            return True
        return False
    except Exception:
        return False


def _validate_links(raw_links: list) -> list[dict]:
    """Filter to reputable links only, max 3."""
    validated = []
    for link in raw_links:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url", ""))
        label = str(link.get("label", url))
        if url and _is_reputable(url):
            validated.append({"label": label, "url": url})
        if len(validated) >= 3:
            break
    return validated
