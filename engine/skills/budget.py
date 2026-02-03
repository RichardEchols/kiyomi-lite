"""
Kiyomi Lite — Budget Tracker Skill
Detects spending, income, and budget mentions in natural conversation.
Extracts amounts, categories, and stores transactions.
"""
import re
from datetime import datetime, timedelta
from calendar import monthrange

try:
    from skills.base import Skill
except ImportError:
    from engine.skills.base import Skill

# ── Keywords ────────────────────────────────────────────────

EXPENSE_KEYWORDS = [
    "spent", "bought", "paid", "cost", "bill", "rent", "mortgage",
    "groceries", "dining", "gas", "subscription", "purchase",
    "charged", "owe", "payment", "expense",
]

INCOME_KEYWORDS = [
    "income", "earned", "salary", "paycheck", "deposit",
    "received", "refund", "bonus", "freelance", "got paid",
]

ALL_KEYWORDS = EXPENSE_KEYWORDS + INCOME_KEYWORDS + [
    "budget", "saving", "savings", "save",
]

# ── Category classification ─────────────────────────────────

CATEGORY_MAP = {
    "groceries": [
        "kroger", "walmart", "aldi", "publix", "safeway", "costco",
        "trader joe", "whole foods", "grocery", "groceries", "food store",
        "supermarket", "heb", "wegmans", "target",
    ],
    "dining": [
        "restaurant", "dining", "dinner", "lunch", "breakfast",
        "coffee", "starbucks", "mcdonald", "chipotle", "pizza",
        "takeout", "take out", "eat out", "ate out", "doordash",
        "uber eats", "grubhub", "bar", "cafe",
    ],
    "transport": [
        "gas", "fuel", "uber", "lyft", "taxi", "parking", "toll",
        "car wash", "mechanic", "auto", "vehicle", "bus", "metro",
        "train", "flight", "airline", "gasoline", "oil change",
    ],
    "entertainment": [
        "movie", "netflix", "spotify", "hulu", "disney", "game",
        "concert", "ticket", "streaming", "subscription", "youtube",
        "apple music", "hbo", "theater", "bowling", "arcade",
    ],
    "bills": [
        "rent", "mortgage", "electric", "water", "internet", "phone",
        "insurance", "utility", "utilities", "bill", "cable", "wifi",
        "cell phone", "power", "sewer",
    ],
    "health": [
        "doctor", "pharmacy", "medicine", "prescription", "hospital",
        "dentist", "therapy", "health", "medical", "copay", "lab",
        "urgent care", "clinic", "vitamin", "supplement",
    ],
    "shopping": [
        "amazon", "clothes", "clothing", "shoes", "mall", "online",
        "order", "shipped", "delivery", "electronics", "appliance",
        "furniture", "home depot", "lowe",
    ],
}

# ── Amount parsing ──────────────────────────────────────────

AMOUNT_PATTERNS = [
    r'\$\s*([\d,]+\.?\d*)',                          # $45 or $45.00 or $1,200
    r'([\d,]+\.?\d*)\s*(?:dollars?|bucks?)',          # 45 dollars, 45 bucks
    r'([\d,]+\.\d{2})\b',                             # 45.00 (with cents)
    r'(?:spent|paid|cost|earned|received|got)\s+(?:about\s+|around\s+|like\s+|another\s+)?([\d,]+\.?\d*)',
]


def _parse_amount(text: str) -> float | None:
    """Extract a dollar amount from text."""
    lower = text.lower()
    for pattern in AMOUNT_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            # Find the first group that matched
            for g in m.groups():
                if g:
                    clean = g.replace(",", "")
                    try:
                        return float(clean)
                    except ValueError:
                        continue
    return None


def _classify_category(text: str) -> str:
    """Auto-classify transaction category from message text."""
    lower = text.lower()
    # Income gets auto-categorized
    if _detect_type(text) == "income":
        for kw in ["salary", "paycheck", "pay check"]:
            if kw in lower:
                return "salary"
        for kw in ["freelance", "contract", "gig", "side"]:
            if kw in lower:
                return "freelance"
        for kw in ["refund", "return"]:
            if kw in lower:
                return "refund"
        for kw in ["bonus"]:
            if kw in lower:
                return "bonus"
        return "income"
    for category, keywords in CATEGORY_MAP.items():
        for kw in keywords:
            if kw in lower:
                return category
    return "other"


def _detect_type(text: str) -> str:
    """Detect if this is income or expense."""
    lower = text.lower()
    for kw in INCOME_KEYWORDS:
        if kw in lower:
            return "income"
    return "expense"


def _extract_note(text: str) -> str:
    """Extract a short note/description from the message."""
    # Try "at <place>" or "on <thing>" or "for <thing>"
    patterns = [
        r'(?:at|from)\s+([A-Za-z\s\']+?)(?:\s+(?:for|today|yesterday|this|last|\d|$))',
        r'(?:for|on)\s+([A-Za-z\s\']+?)(?:\s+(?:at|today|yesterday|this|last|\d|$))',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            note = m.group(1).strip()
            if len(note) > 2:
                return note[:80]
    # Fallback: use first ~60 chars of message
    return text[:60].strip()


# ── Skill class ─────────────────────────────────────────────

class BudgetSkill(Skill):
    name = "budget"
    description = "Tracks spending, income, and budget"

    MAX_TRANSACTIONS = 500

    def detect(self, message: str) -> bool:
        lower = message.lower()
        # Check for any budget keyword
        for kw in ALL_KEYWORDS:
            if kw in lower:
                return True
        # Also detect dollar amounts
        if _parse_amount(message) is not None:
            return True
        return False

    def extract(self, message: str, response: str) -> dict | None:
        amount = _parse_amount(message)
        if amount is None:
            return None

        txn_type = _detect_type(message)
        category = _classify_category(message)
        note = _extract_note(message)

        entry = {
            "type": txn_type,
            "amount": amount,
            "category": category,
            "note": note,
            "date": self.now(),
        }

        return entry

    def get_prompt_context(self) -> str:
        data = self.load_data()
        transactions = data.get("transactions", [])
        if not transactions:
            return "📊 Budget: No transactions logged yet."

        now = datetime.now()
        this_month = now.strftime("%Y-%m")
        last_month_dt = (now.replace(day=1) - timedelta(days=1))
        last_month = last_month_dt.strftime("%Y-%m")

        # This month's transactions
        this_month_txns = [
            t for t in transactions
            if t.get("date", "").startswith(this_month)
        ]
        last_month_txns = [
            t for t in transactions
            if t.get("date", "").startswith(last_month)
        ]

        # Totals by category (expenses only)
        cat_totals: dict[str, float] = {}
        total_expenses = 0.0
        total_income = 0.0
        for t in this_month_txns:
            amt = t.get("amount", 0)
            if t.get("type") == "expense":
                cat = t.get("category", "other")
                cat_totals[cat] = cat_totals.get(cat, 0) + amt
                total_expenses += amt
            else:
                total_income += amt

        last_month_total = sum(
            t.get("amount", 0)
            for t in last_month_txns
            if t.get("type") == "expense"
        )

        # Build context
        lines = [f"📊 Budget — {now.strftime('%B %Y')}:"]
        lines.append(f"  Total expenses: ${total_expenses:,.2f}")
        if total_income > 0:
            lines.append(f"  Total income: ${total_income:,.2f}")

        if cat_totals:
            lines.append("  By category:")
            for cat, amt in sorted(cat_totals.items(), key=lambda x: -x[1]):
                lines.append(f"    • {cat.title()}: ${amt:,.2f}")

        # Top 3 expenses
        expenses = [t for t in this_month_txns if t.get("type") == "expense"]
        top3 = sorted(expenses, key=lambda t: -t.get("amount", 0))[:3]
        if top3:
            lines.append("  Top 3 expenses:")
            for t in top3:
                lines.append(
                    f"    • ${t['amount']:,.2f} — {t.get('note', 'N/A')} ({t.get('category', 'other')})"
                )

        # vs last month
        if last_month_total > 0:
            diff_pct = ((total_expenses - last_month_total) / last_month_total) * 100
            direction = "higher" if diff_pct > 0 else "lower"
            lines.append(
                f"  vs. last month: {abs(diff_pct):.0f}% {direction} (${last_month_total:,.2f})"
            )

        return "\n".join(lines)

    def get_proactive_nudges(self) -> list[str]:
        nudges = []
        data = self.load_data()
        transactions = data.get("transactions", [])
        if not transactions:
            return nudges

        now = datetime.now()
        this_month = now.strftime("%Y-%m")
        last_month_dt = (now.replace(day=1) - timedelta(days=1))
        last_month = last_month_dt.strftime("%Y-%m")

        this_month_expenses = sum(
            t.get("amount", 0)
            for t in transactions
            if t.get("date", "").startswith(this_month) and t.get("type") == "expense"
        )
        last_month_expenses = sum(
            t.get("amount", 0)
            for t in transactions
            if t.get("date", "").startswith(last_month) and t.get("type") == "expense"
        )

        # Spending pace comparison
        if last_month_expenses > 0:
            # Adjust for how far through the month we are
            days_in_month = monthrange(now.year, now.month)[1]
            day_fraction = now.day / days_in_month
            projected = this_month_expenses / max(day_fraction, 0.01)
            if projected > last_month_expenses * 1.2:
                pct = ((projected - last_month_expenses) / last_month_expenses) * 100
                nudges.append(
                    f"💰 Heads up — you're on pace to spend {pct:.0f}% more than last month "
                    f"(${projected:,.0f} projected vs ${last_month_expenses:,.0f})."
                )

        # Recurring bills reminder (detect from past transactions)
        recurring = data.get("recurring", [])
        for bill in recurring:
            due_day = bill.get("day", 1)
            if now.day == due_day - 1 or now.day == due_day:
                nudges.append(
                    f"📋 Reminder: {bill.get('name', 'Bill')} "
                    f"(${bill.get('amount', 0):,.2f}) is due "
                    f"{'today' if now.day == due_day else 'tomorrow'}."
                )

        return nudges
