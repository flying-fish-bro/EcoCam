"""
this is reasoning.py
we gonna use  claude-3-5-sonnet with Anthropic's built-in web_search tool to find
real and purchasable eco-friendly alternatives to the identified objects.
made by rayhan and sasha
"""
import json
import re
import anthropic
from django.conf import settings
# ---------------------------------------------------------------------------
# Prompt builders btw 
# ---------------------------------------------------------------------------
def _build_system_prompt() -> str:
    return """
    
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
given following photographed products
{object_lines}

{price_clause}

search the web for the best eco-friendly, sustainably made alternatives to these
products. 
""".strip()
def find_eco_alternatives(
    objects: list[dict],
    max_price: float | None = None,
) -> list[dict]:
    if not objects:
        return []
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
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
    while True:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=_build_system_prompt(),
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "end_turn":
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "", 
                    }
                )
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            continue
        break
    raw = final_text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    array_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if array_match:
        raw = array_match.group(0)

    try:
        products = json.loads(raw)
        if not isinstance(products, list):
            products = []
    except json.JSONDecodeError:
        products = []
    if max_price is None:
        products = products[:3]
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
def _clamp(value, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return 70
_ALLOWED_DOMAINS = {
    "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au",
    "target.com", "walmart.com", "etsy.com", "ebay.com",
    "bestbuy.com", "wayfair.com", "homedepot.com", "lowes.com",
    "grove.co", "earthhero.com", "packagefreeshop.com",
    "wholefoodsmarket.com", "thegoodtrade.com",
    "rei.com", "patagonia.com", "prAna.com",
    "method.com", "seventhgeneration.com", "ecover.com",
    "packagefreeshop.com", "ecosia.org",
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
