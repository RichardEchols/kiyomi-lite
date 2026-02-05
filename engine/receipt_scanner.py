"""
Kiyomi Lite — Receipt Scanner
Snap a photo of a receipt → extract items/total/merchant → categorize → add to budget.

Usage:
    from receipt_scanner import scan_receipt, process_receipt, get_receipt_history

    result = scan_receipt("/path/to/photo.jpg", config)
    message = process_receipt(result)
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from engine.config import CONFIG_DIR, load_config

logger = logging.getLogger(__name__)

RECEIPTS_DIR = CONFIG_DIR / "receipts"

# ── Receipt-scanning prompt ─────────────────────────────────

RECEIPT_SCAN_PROMPT = """Analyze this receipt image and extract all information into JSON.

Return ONLY valid JSON (no markdown, no code fences, no explanation) with this exact structure:
{
  "is_receipt": true,
  "merchant_name": "Store Name",
  "date": "YYYY-MM-DD",
  "items": [
    {"name": "Item name", "price": 1.99},
    {"name": "Another item", "price": 2.49}
  ],
  "subtotal": 4.48,
  "tax_amount": 0.35,
  "total_amount": 4.83,
  "payment_method": "Visa ending 1234",
  "category": "groceries",
  "confidence": "high"
}

Rules:
- "is_receipt": false if the image is NOT a receipt (return only {"is_receipt": false, "reason": "..."})
- "date": use YYYY-MM-DD format. If not visible, use today's date.
- "items": list every line item you can read. If blurry/unreadable, include what you can with "[unreadable]" for unclear parts.
- "price": numeric only (no $ sign). Use 0.00 if unreadable.
- "subtotal": pre-tax total. Use 0 if not shown.
- "tax_amount": tax amount. Use 0 if not shown.
- "total_amount": final total paid. This is the most important field — get it right.
- "payment_method": credit card type + last 4 digits if visible, "cash", or "unknown".
- "category": classify as ONE of: groceries, dining, shopping, transport, entertainment, health, bills, other.
  - groceries = supermarkets, food stores (Walmart, Kroger, Whole Foods, Target groceries)
  - dining = restaurants, cafes, fast food, bars, coffee shops
  - shopping = clothing, electronics, Amazon, general retail
  - transport = gas stations, auto repair, parking, tolls
  - entertainment = movies, games, streaming, tickets
  - health = pharmacy, doctor, medical supplies
  - bills = utilities, phone, internet
  - other = anything else
- "confidence": "high" if receipt is clear, "medium" if partially readable, "low" if very blurry/damaged.

Be accurate with the total. If you can see the total clearly, that takes priority over summing items."""


# ── Category emoji map ──────────────────────────────────────

CATEGORY_EMOJI = {
    "groceries": "🛒",
    "dining": "🍽️",
    "shopping": "🛍️",
    "transport": "🚗",
    "entertainment": "🎬",
    "health": "💊",
    "bills": "📋",
    "other": "📦",
}


# ── Core functions ──────────────────────────────────────────

def scan_receipt(image_path: str, config: dict | None = None) -> dict:
    """Scan a receipt image using AI vision and extract structured data.

    Args:
        image_path: Path to the receipt image file.
        config: Kiyomi config dict (loads from disk if not provided).

    Returns:
        Dict with receipt fields, or {"is_receipt": false, "reason": "..."} if not a receipt.
        On error, returns {"error": "...", "is_receipt": false}.
    """
    if config is None:
        config = load_config()

    path = Path(image_path)
    if not path.exists():
        return {"error": f"Image not found: {image_path}", "is_receipt": False}

    try:
        image_bytes = path.read_bytes()
    except Exception as e:
        return {"error": f"Could not read image: {e}", "is_receipt": False}

    provider = config.get("provider", "gemini")
    raw_response = ""

    try:
        if provider == "gemini":
            raw_response = _scan_gemini(str(path), config)
        elif provider == "anthropic":
            raw_response = _scan_anthropic(image_bytes, path.suffix, config)
        elif provider == "openai":
            raw_response = _scan_openai(image_bytes, path.suffix, config)
        else:
            # Default to Gemini
            raw_response = _scan_gemini(str(path), config)
    except Exception as e:
        logger.error(f"Receipt scan failed ({provider}): {e}")
        return {"error": f"AI scan failed: {e}", "is_receipt": False}

    # Parse the JSON response
    result = _parse_json_response(raw_response)

    if result is None:
        return {
            "error": "Could not parse AI response as JSON",
            "raw_response": raw_response[:500],
            "is_receipt": False,
        }

    # Normalize fields
    result = _normalize_result(result)
    return result


def process_receipt(scan_result: dict) -> str:
    """Process a scanned receipt: store in budget tracker + save JSON.

    Args:
        scan_result: Dict from scan_receipt().

    Returns:
        Formatted confirmation message for the user.
    """
    if not scan_result.get("is_receipt", False):
        reason = scan_result.get("reason", scan_result.get("error", "not a receipt"))
        return f"🤔 That doesn't look like a receipt — {reason}"

    merchant = scan_result.get("merchant_name", "Unknown")
    total = scan_result.get("total_amount", 0)
    tax = scan_result.get("tax_amount", 0)
    category = scan_result.get("category", "other")
    items = scan_result.get("items", [])
    date_str = scan_result.get("date", datetime.now().strftime("%Y-%m-%d"))
    payment = scan_result.get("payment_method", "unknown")
    confidence = scan_result.get("confidence", "medium")

    # 1. Store in budget tracker
    _store_in_budget(scan_result)

    # 2. Save receipt JSON to ~/.kiyomi/receipts/
    receipt_path = _save_receipt_json(scan_result)

    # 3. Build confirmation message
    emoji = CATEGORY_EMOJI.get(category, "📦")
    month_name = _get_month_name(date_str)

    lines = [f"🧾 Got it! **{merchant}** — ${total:,.2f} ({emoji} {category.title()})"]

    if items:
        # Show up to 6 items, then "and X more..."
        display_items = items[:6]
        item_strs = [f"{it['name']} ${it['price']:,.2f}" for it in display_items]
        items_line = ", ".join(item_strs)
        if len(items) > 6:
            items_line += f", and {len(items) - 6} more..."
        lines.append(f"Items: {items_line}")

    if tax > 0:
        lines.append(f"Tax: ${tax:,.2f}")

    if payment and payment != "unknown":
        lines.append(f"Paid with: {payment}")

    lines.append(f"Added to your {month_name} budget. ✅")

    if confidence == "low":
        lines.append("⚠️ The receipt was hard to read — double-check the amounts!")
    elif confidence == "medium":
        lines.append("💡 Some parts were a bit blurry, but I got the key details.")

    return "\n".join(lines)


def get_receipt_history(days: int = 30) -> str:
    """Get a summary of recently scanned receipts.

    Args:
        days: Number of days to look back (default 30).

    Returns:
        Formatted string with receipt history.
    """
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)

    cutoff = datetime.now() - timedelta(days=days)
    receipts: list[dict] = []

    for f in sorted(RECEIPTS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            date_str = data.get("date", "")
            if date_str:
                receipt_date = datetime.strptime(date_str, "%Y-%m-%d")
                if receipt_date >= cutoff:
                    receipts.append(data)
        except (json.JSONDecodeError, ValueError, OSError):
            continue

    if not receipts:
        return f"📭 No receipts scanned in the last {days} days."

    total_spent = sum(r.get("total_amount", 0) for r in receipts)

    lines = [f"🧾 **Receipt History** (last {days} days)\n"]
    for r in receipts[:15]:  # Show max 15
        merchant = r.get("merchant_name", "Unknown")
        total = r.get("total_amount", 0)
        date = r.get("date", "?")
        cat = r.get("category", "other")
        emoji = CATEGORY_EMOJI.get(cat, "📦")
        lines.append(f"  {emoji} {date} — {merchant}: ${total:,.2f} ({cat})")

    if len(receipts) > 15:
        lines.append(f"  ... and {len(receipts) - 15} more")

    lines.append(f"\n💰 Total from receipts: **${total_spent:,.2f}** ({len(receipts)} receipts)")
    return "\n".join(lines)


# ── AI provider helpers ─────────────────────────────────────

def _scan_gemini(image_path: str, config: dict) -> str:
    """Use Gemini vision to scan receipt."""
    api_key = config.get("gemini_key", "")
    if not api_key:
        raise ValueError("Gemini API key not configured")

    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    uploaded = genai.upload_file(image_path)
    response = model.generate_content([RECEIPT_SCAN_PROMPT, uploaded])
    return response.text or ""


def _scan_anthropic(image_bytes: bytes, suffix: str, config: dict) -> str:
    """Use Claude vision to scan receipt."""
    api_key = config.get("anthropic_key", "")
    if not api_key:
        raise ValueError("Anthropic API key not configured")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    media_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    }
    media_type = media_map.get(suffix.lower(), "image/jpeg")

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": RECEIPT_SCAN_PROMPT},
            ],
        }],
    )
    return response.content[0].text if response.content else ""


def _scan_openai(image_bytes: bytes, suffix: str, config: dict) -> str:
    """Use GPT-4o vision to scan receipt."""
    api_key = config.get("openai_key", "")
    if not api_key:
        raise ValueError("OpenAI API key not configured")

    import openai

    client = openai.OpenAI(api_key=api_key)
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    media_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    }
    media_type = media_map.get(suffix.lower(), "image/jpeg")

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                {"type": "text", "text": RECEIPT_SCAN_PROMPT},
            ],
        }],
    )
    return response.choices[0].message.content or ""


# ── JSON parsing ────────────────────────────────────────────

def _parse_json_response(raw: str) -> dict | None:
    """Parse JSON from AI response, handling markdown code fences and extra text."""
    if not raw:
        return None

    text = raw.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Find first { ... } block
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group())
        except json.JSONDecodeError:
            pass

    return None


# ── Data normalization ──────────────────────────────────────

def _normalize_result(result: dict) -> dict:
    """Ensure all expected fields exist with sane defaults."""
    # Ensure is_receipt is a bool
    result["is_receipt"] = bool(result.get("is_receipt", False))

    if not result["is_receipt"]:
        return result

    # Default date to today if missing/invalid
    date_str = result.get("date", "")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        result["date"] = datetime.now().strftime("%Y-%m-%d")

    # Ensure numeric fields
    for field in ("total_amount", "tax_amount", "subtotal"):
        val = result.get(field, 0)
        try:
            result[field] = round(float(val), 2)
        except (ValueError, TypeError):
            result[field] = 0.0

    # Ensure items is a list of dicts with name+price
    items = result.get("items", [])
    clean_items = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name", "Unknown item"))
            try:
                price = round(float(item.get("price", 0)), 2)
            except (ValueError, TypeError):
                price = 0.0
            clean_items.append({"name": name, "price": price})
    result["items"] = clean_items

    # Default strings
    result.setdefault("merchant_name", "Unknown")
    result.setdefault("payment_method", "unknown")
    result.setdefault("confidence", "medium")

    # Validate category
    valid_categories = {"groceries", "dining", "shopping", "transport", "entertainment", "health", "bills", "other"}
    if result.get("category", "").lower() not in valid_categories:
        result["category"] = "other"
    else:
        result["category"] = result["category"].lower()

    return result


# ── Budget integration ──────────────────────────────────────

def _store_in_budget(scan_result: dict):
    """Store the receipt total as a budget transaction."""
    try:
        from skills.budget import BudgetSkill
    except ImportError:
        try:
            from engine.skills.budget import BudgetSkill
        except ImportError:
            logger.warning("BudgetSkill not available — skipping budget storage")
            return

    merchant = scan_result.get("merchant_name", "Unknown")
    total = scan_result.get("total_amount", 0)
    category = scan_result.get("category", "other")
    date_str = scan_result.get("date", datetime.now().strftime("%Y-%m-%d"))

    entry = {
        "type": "expense",
        "amount": total,
        "category": category,
        "note": f"Receipt: {merchant}",
        "date": f"{date_str} {datetime.now().strftime('%H:%M')}",
        "source": "receipt_scan",
    }

    skill = BudgetSkill()
    skill.store("transactions", entry, max_per_category=skill.MAX_TRANSACTIONS)
    logger.info(f"Stored receipt in budget: {merchant} ${total:.2f} ({category})")


# ── Receipt JSON storage ───────────────────────────────────

def _save_receipt_json(scan_result: dict) -> Path:
    """Save receipt data to ~/.kiyomi/receipts/YYYY-MM-DD_merchant.json"""
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)

    date_str = scan_result.get("date", datetime.now().strftime("%Y-%m-%d"))
    merchant = scan_result.get("merchant_name", "unknown")

    # Sanitize merchant name for filename
    safe_merchant = re.sub(r"[^a-zA-Z0-9_\-]", "_", merchant.lower())[:40]
    filename = f"{date_str}_{safe_merchant}.json"

    # Avoid overwriting — add suffix if file exists
    filepath = RECEIPTS_DIR / filename
    counter = 1
    while filepath.exists():
        name_stem = f"{date_str}_{safe_merchant}_{counter}"
        filepath = RECEIPTS_DIR / f"{name_stem}.json"
        counter += 1

    # Add metadata
    save_data = {
        **scan_result,
        "scanned_at": datetime.now().isoformat(),
    }

    filepath.write_text(json.dumps(save_data, indent=2, default=str), encoding="utf-8")
    logger.info(f"Saved receipt to {filepath}")
    return filepath


# ── Helpers ─────────────────────────────────────────────────

def _get_month_name(date_str: str) -> str:
    """Get month name from a YYYY-MM-DD date string."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%B")
    except (ValueError, TypeError):
        return datetime.now().strftime("%B")


# ── Receipt detection helper ────────────────────────────────

RECEIPT_KEYWORDS = {
    "receipt", "scan receipt", "scan this receipt", "receipt scan",
    "scanned receipt", "what did i spend", "how much was this",
    "log this receipt", "add this receipt", "read this receipt",
    "what's on this receipt",
}


def looks_like_receipt_request(caption: str | None, recent_messages: list[str] | None = None) -> bool:
    """Check if the user is requesting a receipt scan based on caption or recent context.

    Args:
        caption: Photo caption text (may be None).
        recent_messages: Last few user messages for context (may be None).

    Returns:
        True if this looks like a receipt scan request.
    """
    # Check caption
    if caption:
        caption_lower = caption.lower().strip()
        for kw in RECEIPT_KEYWORDS:
            if kw in caption_lower:
                return True

    # Check recent messages for receipt context
    if recent_messages:
        for msg in recent_messages[-3:]:  # Last 3 messages
            msg_lower = msg.lower()
            for kw in RECEIPT_KEYWORDS:
                if kw in msg_lower:
                    return True

    return False
