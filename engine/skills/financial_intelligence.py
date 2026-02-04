"""
Kiyomi Lite — Smart Financial Intelligence Skill
The premium feature that makes Kiyomi worth $19/month.

Capabilities:
  1. Bill Detector — finds recurring charges, predicts next dates
  2. Spending Alerts — flags 20%+ spending spikes by category
  3. Savings Goal Tracker — set goals, track income-minus-spending progress
  4. Money Personality Insights — categorizes spending patterns
  5. Weekly Financial Report — formatted markdown digest
"""

import json
import logging
import re
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

from engine.skills.base import Skill
from engine.plaid_integration import get_transactions, get_balances, is_bank_connected
from engine.config import load_config

log = logging.getLogger("kiyomi.financial_intelligence")

# ── Storage ─────────────────────────────────────────────────

KIYOMI_DIR = Path.home() / ".kiyomi"
GOALS_FILE = KIYOMI_DIR / "financial_goals.json"


def _load_goals() -> list[dict]:
    """Load savings goals from disk."""
    if GOALS_FILE.exists():
        try:
            return json.loads(GOALS_FILE.read_text())
        except (json.JSONDecodeError, IOError) as e:
            log.warning("Failed to load goals: %s", e)
    return []


def _save_goals(goals: list[dict]):
    """Persist savings goals to disk."""
    KIYOMI_DIR.mkdir(parents=True, exist_ok=True)
    GOALS_FILE.write_text(json.dumps(goals, indent=2, default=str))


# ── Intent Detection Keywords ───────────────────────────────

BILL_KEYWORDS = [
    "bills", "bill", "recurring", "subscription", "subscriptions",
    "recurring charges", "monthly charges", "what do i pay for",
    "what am i subscribed to", "detect bills", "upcoming bills",
    "next bill", "when is my", "auto-pay", "autopay",
]

SPENDING_ALERT_KEYWORDS = [
    "spending alert", "spending alerts", "am i overspending",
    "over budget", "spending too much", "spending spike",
    "spending compared", "vs last month", "how am i doing",
    "spending check", "check my spending", "net worth",
    "how much did i spend", "what did i spend", "my spending",
    "my balance", "my finances", "financial overview",
    "money status", "bank balance", "account balance",
]

SAVINGS_KEYWORDS = [
    "savings goal", "save goal", "saving goal",
    "set a goal", "set goal", "my goal",
    "goal progress", "how much have i saved",
    "am i on track", "savings progress", "savings tracker",
    "save $", "save money",
]

PERSONALITY_KEYWORDS = [
    "money personality", "spending personality", "what kind of spender",
    "spending habits", "spending style", "spending patterns",
    "what do i spend on", "where does my money go",
    "money type", "spender type", "financial personality",
]

REPORT_KEYWORDS = [
    "financial report", "weekly report", "money report",
    "financial summary", "weekly summary", "finance summary",
    "give me a report", "show me my finances", "full report",
    "financial overview", "money overview",
]

ALL_KEYWORDS = (
    BILL_KEYWORDS + SPENDING_ALERT_KEYWORDS + SAVINGS_KEYWORDS
    + PERSONALITY_KEYWORDS + REPORT_KEYWORDS
)

# ── Amount Parsing ──────────────────────────────────────────

_AMOUNT_RE = [
    re.compile(r'\$\s*([\d,]+\.?\d*)'),
    re.compile(r'([\d,]+\.?\d*)\s*(?:dollars?|bucks?)'),
    re.compile(r'([\d,]+\.\d{2})\b'),
]

_GOAL_SET_RE = re.compile(
    r'(?:save|savings?\s*goal)\s*(?:of\s*)?\$?\s*([\d,]+\.?\d*)'
    r'(?:\s+(?:this|by\s+end\s+of)\s+(month|week))?',
    re.IGNORECASE,
)

_GOAL_PERIOD_RE = re.compile(
    r'(?:this|by\s+end\s+of)\s+(month|week)',
    re.IGNORECASE,
)


def _parse_amount(text: str) -> Optional[float]:
    for pat in _AMOUNT_RE:
        m = pat.search(text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


# ── Plaid Data Helpers ──────────────────────────────────────

def _get_plaid_creds() -> tuple[str, str, str]:
    """Return (client_id, secret, env) from Kiyomi config."""
    cfg = load_config()
    plaid_cfg = cfg.get("plaid", {})
    return (
        plaid_cfg.get("client_id", ""),
        plaid_cfg.get("secret", ""),
        plaid_cfg.get("env", "sandbox"),
    )


def _fetch_transactions(days: int = 90) -> list[dict]:
    """Fetch transactions from Plaid, return empty list on failure."""
    cid, secret, env = _get_plaid_creds()
    if not cid or not secret:
        log.warning("Plaid credentials not configured")
        return []
    data = get_transactions(cid, secret, env, days=days)
    if "error" in data:
        log.warning("Plaid transaction fetch error: %s", data["error"])
        return []
    return data.get("transactions", [])


def _fetch_balances() -> dict:
    """Fetch account balances from Plaid."""
    cid, secret, env = _get_plaid_creds()
    if not cid or not secret:
        return {}
    data = get_balances(cid, secret, env)
    if "error" in data:
        log.warning("Plaid balance fetch error: %s", data["error"])
        return {}
    return data


# ── 1. Bill Detector ────────────────────────────────────────

# Known subscription/bill merchant patterns for fuzzy matching
_KNOWN_SUBSCRIPTIONS = {
    "netflix", "spotify", "hulu", "disney", "apple", "amazon prime",
    "youtube", "hbo", "paramount", "peacock", "crunchyroll",
    "adobe", "microsoft", "google storage", "icloud", "dropbox",
    "chatgpt", "openai", "claude", "midjourney",
    "gym", "planet fitness", "la fitness", "ymca",
    "att", "t-mobile", "verizon", "xfinity", "comcast", "spectrum",
    "geico", "state farm", "progressive", "allstate",
    "rent", "mortgage",
}


def detect_bills(transactions: Optional[list[dict]] = None, min_occurrences: int = 2) -> list[dict]:
    """Analyze transactions to find recurring charges.

    Groups by merchant, detects monthly/weekly patterns, estimates
    next charge date.

    Returns a list of dicts:
        {
            "merchant": str,
            "amount": float,          # most recent charge
            "avg_amount": float,
            "frequency": "monthly" | "weekly" | "biweekly" | "quarterly" | "irregular",
            "occurrences": int,
            "dates": [str],           # ISO date strings of past charges
            "next_expected": str,     # ISO date of predicted next charge
            "category": str,
            "confidence": float,      # 0-1
        }
    """
    if transactions is None:
        transactions = _fetch_transactions(days=120)

    if not transactions:
        return []

    # Group by normalized merchant
    by_merchant: dict[str, list[dict]] = defaultdict(list)
    for t in transactions:
        # Plaid: positive amounts = money out (expenses)
        if t.get("amount", 0) <= 0:
            continue
        merchant = (t.get("merchant") or t.get("name") or "").strip()
        if not merchant:
            continue
        key = _normalize_merchant(merchant)
        by_merchant[key].append(t)

    recurring = []
    today = date.today()

    for merchant_key, txns in by_merchant.items():
        if len(txns) < min_occurrences:
            continue

        # Sort by date ascending
        txns.sort(key=lambda t: t.get("date", ""))
        amounts = [t["amount"] for t in txns]
        dates_str = [t["date"] for t in txns]
        dates = []
        for ds in dates_str:
            try:
                dates.append(datetime.strptime(ds, "%Y-%m-%d").date())
            except (ValueError, TypeError):
                continue

        if len(dates) < min_occurrences:
            continue

        # Compute gaps between charges (in days)
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        if not gaps:
            continue

        avg_gap = sum(gaps) / len(gaps)
        avg_amount = sum(amounts) / len(amounts)
        amount_variance = max(amounts) - min(amounts) if len(amounts) > 1 else 0

        # Classify frequency
        frequency, confidence = _classify_frequency(gaps, avg_gap)

        # Amount consistency bonus (recurring bills have consistent amounts)
        if amount_variance < avg_amount * 0.1:
            confidence = min(1.0, confidence + 0.15)

        # Known subscription bonus
        if any(sub in merchant_key for sub in _KNOWN_SUBSCRIPTIONS):
            confidence = min(1.0, confidence + 0.2)

        # Skip if confidence too low
        if confidence < 0.3:
            continue

        # Predict next charge
        last_date = dates[-1]
        next_expected = _predict_next_date(last_date, frequency, avg_gap)

        display_merchant = txns[-1].get("merchant") or txns[-1].get("name", merchant_key)

        recurring.append({
            "merchant": display_merchant,
            "amount": amounts[-1],
            "avg_amount": round(avg_amount, 2),
            "frequency": frequency,
            "occurrences": len(txns),
            "dates": dates_str,
            "next_expected": next_expected.isoformat() if next_expected else None,
            "category": txns[-1].get("category", "Other"),
            "confidence": round(confidence, 2),
        })

    # Sort by confidence descending, then amount
    recurring.sort(key=lambda b: (-b["confidence"], -b["amount"]))
    return recurring


def _normalize_merchant(name: str) -> str:
    """Normalize merchant name for grouping."""
    lower = name.lower().strip()
    # Remove trailing numbers, asterisks, location info
    lower = re.sub(r'[#*]\d+', '', lower)
    lower = re.sub(r'\s+\d{3,}', '', lower)
    lower = re.sub(r'\s*(inc|llc|ltd|corp|co)\b\.?', '', lower)
    lower = re.sub(r'\s+', ' ', lower).strip()
    return lower


def _classify_frequency(gaps: list[int], avg_gap: float) -> tuple[str, float]:
    """Classify charge frequency and return (frequency, confidence)."""
    if not gaps:
        return "irregular", 0.0

    # Standard deviation of gaps
    variance = sum((g - avg_gap) ** 2 for g in gaps) / len(gaps)
    std_dev = variance ** 0.5
    consistency = 1.0 - min(std_dev / max(avg_gap, 1), 1.0)

    if 25 <= avg_gap <= 35:
        return "monthly", 0.4 + consistency * 0.5
    elif 5 <= avg_gap <= 9:
        return "weekly", 0.4 + consistency * 0.5
    elif 12 <= avg_gap <= 16:
        return "biweekly", 0.35 + consistency * 0.5
    elif 85 <= avg_gap <= 100:
        return "quarterly", 0.3 + consistency * 0.4
    elif 355 <= avg_gap <= 375:
        return "annual", 0.3 + consistency * 0.4
    else:
        return "irregular", max(0.1, consistency * 0.3)


def _predict_next_date(last_date: date, frequency: str, avg_gap: float) -> Optional[date]:
    """Predict next charge date based on frequency."""
    gap_map = {
        "weekly": 7,
        "biweekly": 14,
        "monthly": 30,
        "quarterly": 91,
        "annual": 365,
    }
    gap = gap_map.get(frequency)
    if gap is None:
        gap = round(avg_gap)
    if gap <= 0:
        return None

    predicted = last_date + timedelta(days=gap)
    today = date.today()

    # If predicted date is in the past, advance to next cycle
    while predicted < today:
        predicted += timedelta(days=gap)

    return predicted


# ── 2. Spending Alerts ──────────────────────────────────────

def check_spending_alerts(
    threshold_pct: float = 0.20,
    transactions: Optional[list[dict]] = None,
) -> list[dict]:
    """Compare current-period spending vs previous period by category.

    Flags categories where spending is ≥ threshold_pct above the
    previous period.

    Returns list of:
        {
            "category": str,
            "current_amount": float,
            "previous_amount": float,
            "difference": float,
            "pct_change": float,
            "message": str,   # Human-readable alert
        }
    """
    if transactions is None:
        transactions = _fetch_transactions(days=62)

    if not transactions:
        return []

    today = date.today()
    first_of_month = today.replace(day=1)
    if first_of_month.month == 1:
        first_of_prev = first_of_month.replace(year=first_of_month.year - 1, month=12)
    else:
        first_of_prev = first_of_month.replace(month=first_of_month.month - 1)

    current_by_cat: dict[str, float] = defaultdict(float)
    previous_by_cat: dict[str, float] = defaultdict(float)

    for t in transactions:
        if t.get("amount", 0) <= 0:
            continue
        try:
            txn_date = datetime.strptime(t["date"], "%Y-%m-%d").date()
        except (ValueError, TypeError, KeyError):
            continue

        cat = t.get("category", "Other")

        if txn_date >= first_of_month:
            current_by_cat[cat] += t["amount"]
        elif txn_date >= first_of_prev:
            previous_by_cat[cat] += t["amount"]

    # Scale previous month to current day-of-month for fair comparison
    days_in_month = monthrange(today.year, today.month)[1]
    day_fraction = today.day / days_in_month
    # Only compare if we're at least 7 days in (avoid noise early in month)
    if today.day < 7:
        day_fraction = max(day_fraction, 7 / days_in_month)

    alerts = []
    all_cats = set(current_by_cat) | set(previous_by_cat)

    for cat in all_cats:
        current = current_by_cat.get(cat, 0)
        previous = previous_by_cat.get(cat, 0)

        if previous < 5:
            # Skip categories with negligible previous spending
            # (new category this month isn't really a "spike")
            if current > 50:
                alerts.append({
                    "category": cat,
                    "current_amount": round(current, 2),
                    "previous_amount": round(previous, 2),
                    "difference": round(current, 2),
                    "pct_change": 0,
                    "message": (
                        f"🆕 New spending category: **{cat}** — "
                        f"${current:,.2f} this month (no spending last month)"
                    ),
                })
            continue

        # Projected vs actual previous month
        projected_current = current / max(day_fraction, 0.01)
        pct_change = (projected_current - previous) / previous

        if pct_change >= threshold_pct:
            diff = current - (previous * day_fraction)
            emoji = "⚠️" if pct_change < 0.5 else "🚨"
            alerts.append({
                "category": cat,
                "current_amount": round(current, 2),
                "previous_amount": round(previous, 2),
                "difference": round(diff, 2),
                "pct_change": round(pct_change, 4),
                "message": (
                    f"{emoji} You've spent **${current:,.2f}** on **{cat}** this month — "
                    f"that's ${abs(diff):,.2f} more than this point last month "
                    f"(${previous:,.2f} total last month). "
                    f"On pace for **${projected_current:,.0f}** ({pct_change * 100:.0f}% above normal)."
                ),
            })

    # Sort by percentage change descending
    alerts.sort(key=lambda a: -a.get("pct_change", 0))
    return alerts


# ── 3. Savings Goal Tracker ─────────────────────────────────

def set_savings_goal(
    amount: float,
    period: str = "month",
    name: str = "Savings Goal",
) -> dict:
    """Create or update a savings goal.

    Args:
        amount: Target amount to save
        period: "month" or "week"
        name: Label for the goal

    Returns the created goal dict.
    """
    today = date.today()

    if period == "week":
        # End of current week (Sunday)
        days_until_sunday = (6 - today.weekday()) % 7
        if days_until_sunday == 0:
            days_until_sunday = 7
        end_date = today + timedelta(days=days_until_sunday)
        start_date = end_date - timedelta(days=6)
    else:
        # End of current month
        start_date = today.replace(day=1)
        days_in_month = monthrange(today.year, today.month)[1]
        end_date = today.replace(day=days_in_month)

    goal = {
        "name": name,
        "target": amount,
        "period": period,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "created": datetime.now().isoformat(),
        "active": True,
    }

    goals = _load_goals()
    # Deactivate any existing goal with the same period
    for g in goals:
        if g.get("period") == period and g.get("active"):
            g["active"] = False
    goals.append(goal)
    _save_goals(goals)

    return goal


def get_goal_progress(transactions: Optional[list[dict]] = None) -> list[dict]:
    """Check progress on all active savings goals.

    Calculates income - spending for the goal period and compares
    against the target.

    Returns list of:
        {
            "name": str,
            "target": float,
            "saved": float,           # income - expenses in period
            "income": float,
            "expenses": float,
            "pct_complete": float,     # 0-100
            "days_remaining": int,
            "daily_target": float,     # how much to save per remaining day
            "on_track": bool,
            "message": str,
        }
    """
    goals = _load_goals()
    active = [g for g in goals if g.get("active")]

    if not active:
        return []

    if transactions is None:
        transactions = _fetch_transactions(days=45)

    today = date.today()
    results = []

    for goal in active:
        try:
            start = datetime.strptime(goal["start_date"], "%Y-%m-%d").date()
            end = datetime.strptime(goal["end_date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue

        # Auto-expire goals
        if end < today:
            goal["active"] = False
            continue

        target = goal.get("target", 0)
        income = 0.0
        expenses = 0.0

        for t in transactions:
            try:
                txn_date = datetime.strptime(t["date"], "%Y-%m-%d").date()
            except (ValueError, TypeError, KeyError):
                continue
            if not (start <= txn_date <= today):
                continue

            amt = t.get("amount", 0)
            if amt > 0:
                expenses += amt
            else:
                income += abs(amt)

        saved = income - expenses
        pct = (saved / target * 100) if target > 0 else 0
        days_remaining = max((end - today).days, 1)
        shortfall = max(target - saved, 0)
        daily_target = shortfall / days_remaining if days_remaining > 0 else 0
        total_days = max((end - start).days, 1)
        expected_pct = ((today - start).days / total_days) * 100
        on_track = pct >= expected_pct * 0.85  # 15% grace

        # Build message
        if pct >= 100:
            emoji = "🎉"
            status = f"Goal reached! You've saved ${saved:,.2f} of ${target:,.2f}!"
        elif on_track:
            emoji = "✅"
            status = (
                f"On track! ${saved:,.2f} of ${target:,.2f} saved "
                f"({pct:.0f}%). {days_remaining} days left — "
                f"need ~${daily_target:,.2f}/day to hit your goal."
            )
        else:
            emoji = "📊"
            status = (
                f"${saved:,.2f} of ${target:,.2f} saved ({pct:.0f}%). "
                f"You're a bit behind — need ~${daily_target:,.2f}/day "
                f"for the remaining {days_remaining} days."
            )

        results.append({
            "name": goal.get("name", "Savings Goal"),
            "target": target,
            "saved": round(saved, 2),
            "income": round(income, 2),
            "expenses": round(expenses, 2),
            "pct_complete": round(min(pct, 100), 1),
            "days_remaining": days_remaining,
            "daily_target": round(daily_target, 2),
            "on_track": on_track,
            "message": f"{emoji} **{goal.get('name', 'Savings Goal')}:** {status}",
        })

    # Persist any deactivated goals
    _save_goals(goals)
    return results


# ── 4. Money Personality Insights ───────────────────────────

# Category groups for personality analysis
_PERSONALITY_CATEGORIES = {
    "foodie": {
        "label": "Foodie 🍕",
        "tagline": "You love good food — and it shows in your spending.",
        "categories": ["Food and Drink", "Restaurants", "Fast Food", "Coffee Shops"],
    },
    "homebody": {
        "label": "Homebody 🏠",
        "tagline": "Your castle is your kingdom. You invest in comfort.",
        "categories": ["Shops", "Home", "Home Improvement", "Furniture", "Utilities"],
    },
    "adventurer": {
        "label": "Adventurer ✈️",
        "tagline": "Experiences over things — you're always on the move.",
        "categories": ["Travel", "Airlines", "Hotels", "Recreation", "Entertainment"],
    },
    "techie": {
        "label": "Techie 💻",
        "tagline": "Gadgets, apps, and subscriptions — your digital life is strong.",
        "categories": ["Electronics", "Software", "Digital Purchase", "Subscriptions"],
    },
    "fashionista": {
        "label": "Fashionista 👗",
        "tagline": "Looking good isn't cheap, and you know it.",
        "categories": ["Clothing", "Apparel", "Beauty", "Personal Care"],
    },
    "wellness": {
        "label": "Wellness Warrior 🧘",
        "tagline": "You invest in feeling good — body, mind, and spirit.",
        "categories": ["Healthcare", "Fitness", "Gym", "Pharmacy", "Medical"],
    },
    "auto_enthusiast": {
        "label": "Road Warrior 🚗",
        "tagline": "You keep those wheels turning — gas, maintenance, the works.",
        "categories": ["Automotive", "Gas", "Gas Stations", "Car", "Parking"],
    },
}

# Categories considered discretionary (excludes bills/utilities/rent)
_NON_DISCRETIONARY = {
    "Rent", "Mortgage", "Insurance", "Taxes", "Utilities",
    "Interest", "Loan", "Transfer", "Payment",
}


def get_money_personality(transactions: Optional[list[dict]] = None) -> dict:
    """Analyze spending patterns and return personality insights.

    Returns:
        {
            "primary": {"type": str, "label": str, "tagline": str, "pct": float},
            "secondary": {...} | None,
            "breakdown": {category: pct, ...},
            "insights": [str, ...],
            "total_discretionary": float,
        }
    """
    if transactions is None:
        transactions = _fetch_transactions(days=90)

    if not transactions:
        return {
            "primary": None,
            "secondary": None,
            "breakdown": {},
            "insights": ["Not enough transaction data yet. Connect your bank and check back in a week!"],
            "total_discretionary": 0,
        }

    # Sum expenses by category, splitting discretionary vs not
    disc_by_cat: dict[str, float] = defaultdict(float)
    total_discretionary = 0.0
    total_spent = 0.0

    for t in transactions:
        if t.get("amount", 0) <= 0:
            continue
        cat = t.get("category", "Other")
        total_spent += t["amount"]
        if cat not in _NON_DISCRETIONARY:
            disc_by_cat[cat] += t["amount"]
            total_discretionary += t["amount"]

    if total_discretionary < 10:
        return {
            "primary": None,
            "secondary": None,
            "breakdown": {},
            "insights": ["Not enough discretionary spending data to analyze yet."],
            "total_discretionary": 0,
        }

    # Score each personality type
    scores: dict[str, float] = {}
    for ptype, info in _PERSONALITY_CATEGORIES.items():
        type_total = 0.0
        for cat, amount in disc_by_cat.items():
            cat_lower = cat.lower()
            if any(pc.lower() in cat_lower or cat_lower in pc.lower()
                   for pc in info["categories"]):
                type_total += amount
        scores[ptype] = type_total / total_discretionary if total_discretionary > 0 else 0

    # Sort by score descending
    ranked = sorted(scores.items(), key=lambda x: -x[1])

    # Top category breakdown
    cat_pcts = {
        cat: round(amt / total_discretionary * 100, 1)
        for cat, amt in sorted(disc_by_cat.items(), key=lambda x: -x[1])[:8]
    }

    # Build result
    primary_type = ranked[0] if ranked[0][1] > 0.05 else None
    secondary_type = ranked[1] if len(ranked) > 1 and ranked[1][1] > 0.05 else None

    def _build_type(pair):
        if pair is None:
            return None
        ptype, score = pair
        info = _PERSONALITY_CATEGORIES[ptype]
        return {
            "type": ptype,
            "label": info["label"],
            "tagline": info["tagline"],
            "pct": round(score * 100, 1),
        }

    # Generate natural-language insights
    insights = []
    if primary_type:
        info = _PERSONALITY_CATEGORIES[primary_type[0]]
        insights.append(
            f"You're a **{info['label']}** — "
            f"{primary_type[1] * 100:.0f}% of your discretionary spending "
            f"goes to {', '.join(info['categories'][:3])}."
        )
    if secondary_type:
        info = _PERSONALITY_CATEGORIES[secondary_type[0]]
        insights.append(
            f"Your secondary trait is **{info['label']}** "
            f"({secondary_type[1] * 100:.0f}% of discretionary spending)."
        )

    # Fun insights based on data
    if disc_by_cat:
        top_cat = max(disc_by_cat, key=disc_by_cat.get)
        top_pct = disc_by_cat[top_cat] / total_discretionary * 100
        insights.append(
            f"Your #1 spending category is **{top_cat}** "
            f"at **{top_pct:.0f}%** of discretionary spending."
        )

    # Savings rate if we have income
    income = sum(abs(t["amount"]) for t in transactions if t.get("amount", 0) < 0)
    if income > 0:
        savings_rate = ((income - total_spent) / income) * 100
        if savings_rate > 20:
            insights.append(f"💪 Impressive {savings_rate:.0f}% savings rate!")
        elif savings_rate > 0:
            insights.append(f"Your savings rate is {savings_rate:.0f}%.")
        else:
            insights.append(
                "You're spending more than you're earning this period. "
                "Time to tighten up? 👀"
            )

    return {
        "primary": _build_type(primary_type),
        "secondary": _build_type(secondary_type),
        "breakdown": cat_pcts,
        "insights": insights,
        "total_discretionary": round(total_discretionary, 2),
    }


# ── 5. Weekly Financial Report ──────────────────────────────

def generate_weekly_report(transactions: Optional[list[dict]] = None) -> str:
    """Generate a full formatted markdown weekly financial report.

    Combines all intelligence features into one digest:
    - Total income & expenses
    - Top spending categories
    - Notable transactions
    - Spending alerts
    - Bill reminders
    - Goal progress
    - Money personality snippet

    Returns a markdown-formatted string.
    """
    # Fetch data once, share across analyses
    if transactions is None:
        transactions = _fetch_transactions(days=60)

    today = date.today()
    week_ago = today - timedelta(days=7)
    month_start = today.replace(day=1)

    # ── Filter to this week ──
    week_txns = []
    month_txns = []
    for t in transactions:
        try:
            txn_date = datetime.strptime(t["date"], "%Y-%m-%d").date()
        except (ValueError, TypeError, KeyError):
            continue
        if txn_date >= week_ago:
            week_txns.append(t)
        if txn_date >= month_start:
            month_txns.append(t)

    # ── Compute totals ──
    week_income = sum(abs(t["amount"]) for t in week_txns if t.get("amount", 0) < 0)
    week_expenses = sum(t["amount"] for t in week_txns if t.get("amount", 0) > 0)
    month_income = sum(abs(t["amount"]) for t in month_txns if t.get("amount", 0) < 0)
    month_expenses = sum(t["amount"] for t in month_txns if t.get("amount", 0) > 0)

    # ── Top categories (this week) ──
    cat_totals: dict[str, float] = defaultdict(float)
    for t in week_txns:
        if t.get("amount", 0) > 0:
            cat_totals[t.get("category", "Other")] += t["amount"]
    sorted_cats = sorted(cat_totals.items(), key=lambda x: -x[1])[:6]

    # ── Notable transactions (largest this week) ──
    expenses_only = [t for t in week_txns if t.get("amount", 0) > 0]
    notable = sorted(expenses_only, key=lambda t: -t.get("amount", 0))[:5]

    # ── Build report ──
    lines = []
    lines.append(f"# 💰 Weekly Financial Report")
    lines.append(f"**{week_ago.strftime('%b %d')} — {today.strftime('%b %d, %Y')}**\n")

    # Summary
    lines.append("## 📊 Summary")
    lines.append(f"| | This Week | Month to Date |")
    lines.append(f"|---|---|---|")
    lines.append(f"| 💵 Income | ${week_income:,.2f} | ${month_income:,.2f} |")
    lines.append(f"| 💸 Spent | ${week_expenses:,.2f} | ${month_expenses:,.2f} |")
    net_week = week_income - week_expenses
    net_month = month_income - month_expenses
    net_emoji_w = "📈" if net_week >= 0 else "📉"
    net_emoji_m = "📈" if net_month >= 0 else "📉"
    lines.append(
        f"| {net_emoji_w} Net | ${net_week:,.2f} | ${net_month:,.2f} |"
    )
    lines.append("")

    # Top categories
    if sorted_cats:
        lines.append("## 🏷️ Top Categories This Week")
        for cat, amt in sorted_cats:
            pct = (amt / week_expenses * 100) if week_expenses > 0 else 0
            bar = "█" * max(1, int(pct / 5)) + "░" * max(0, 20 - int(pct / 5))
            lines.append(f"- **{cat}**: ${amt:,.2f} ({pct:.0f}%) `{bar}`")
        lines.append("")

    # Notable transactions
    if notable:
        lines.append("## 🔍 Notable Transactions")
        for t in notable:
            merchant = t.get("merchant") or t.get("name", "Unknown")
            lines.append(
                f"- ${t['amount']:,.2f} — {merchant} "
                f"({t.get('category', 'Other')}) · {t['date']}"
            )
        lines.append("")

    # Spending alerts
    alerts = check_spending_alerts(transactions=transactions)
    if alerts:
        lines.append("## ⚠️ Spending Alerts")
        for alert in alerts[:3]:
            lines.append(f"- {alert['message']}")
        lines.append("")

    # Upcoming bills
    bills = detect_bills(transactions=transactions)
    upcoming_bills = [
        b for b in bills
        if b.get("next_expected") and b["confidence"] >= 0.5
    ]
    # Sort by next expected date
    upcoming_bills.sort(key=lambda b: b.get("next_expected", "9999"))
    upcoming_soon = [
        b for b in upcoming_bills
        if b.get("next_expected") and b["next_expected"] <= (today + timedelta(days=14)).isoformat()
    ]
    if upcoming_soon:
        lines.append("## 📅 Upcoming Bills (Next 2 Weeks)")
        for b in upcoming_soon[:8]:
            lines.append(
                f"- **{b['merchant']}**: ~${b['avg_amount']:,.2f} "
                f"— expected {b['next_expected']} ({b['frequency']})"
            )
        lines.append("")

    # Goal progress
    goal_progress = get_goal_progress(transactions=transactions)
    if goal_progress:
        lines.append("## 🎯 Savings Goals")
        for gp in goal_progress:
            pct = gp["pct_complete"]
            filled = int(pct / 5)
            bar = "🟩" * filled + "⬜" * (20 - filled)
            lines.append(f"- {gp['message']}")
            lines.append(f"  `{bar}` {pct:.0f}%")
        lines.append("")

    # Money personality (quick snippet)
    personality = get_money_personality(transactions=transactions)
    if personality.get("primary"):
        lines.append("## 🧠 Your Money Personality")
        for insight in personality["insights"][:2]:
            lines.append(f"- {insight}")
        lines.append("")

    # Balance snapshot
    balances = _fetch_balances()
    if balances and "accounts" in balances:
        lines.append("## 🏦 Account Balances")
        for acct in balances["accounts"]:
            emoji = {
                "depository": "💵",
                "credit": "💳",
                "investment": "📊",
                "loan": "🏠",
            }.get(acct.get("type", ""), "🏦")
            mask = f" (•••{acct['mask']})" if acct.get("mask") else ""
            lines.append(f"- {emoji} **{acct['name']}**{mask}: ${acct['balance']:,.2f}")
        if "net_worth" in balances:
            lines.append(f"\n**Net Worth:** ${balances['net_worth']:,.2f}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by Kiyomi Financial Intelligence · {datetime.now().strftime('%Y-%m-%d %H:%M')}*")

    return "\n".join(lines)


# ── Skill Class ─────────────────────────────────────────────

class FinancialIntelligenceSkill(Skill):
    """Smart Financial Intelligence — the premium Kiyomi skill.

    Detects financial intents and routes to the appropriate
    intelligence module: bills, alerts, goals, personality, reports.
    """

    name = "financial_intelligence"
    description = (
        "Smart financial insights: bill detection, spending alerts, "
        "savings goals, money personality, weekly reports"
    )

    def detect(self, message: str) -> bool:
        """Does this message relate to financial intelligence?"""
        lower = message.lower()
        return any(kw in lower for kw in ALL_KEYWORDS)

    def extract(self, message: str, response: str) -> dict | None:
        """Pull structured data from the conversation.

        Detects intent and returns the appropriate data payload.
        """
        return self.process(message)

    def process(self, message: str) -> dict | None:
        """Main intent router. Detect what the user wants and respond.

        Returns a dict with:
            {
                "skill": "financial_intelligence",
                "intent": str,
                "data": ...,
                "message": str,   # Ready-to-send formatted response
            }
        """
        lower = message.lower()

        # ── Savings goal creation ──
        goal_match = _GOAL_SET_RE.search(message)
        if goal_match and any(kw in lower for kw in SAVINGS_KEYWORDS):
            amount = float(goal_match.group(1).replace(",", ""))
            period_match = _GOAL_PERIOD_RE.search(message)
            period = period_match.group(1).lower() if period_match else "month"
            goal = set_savings_goal(amount, period)
            return {
                "skill": self.name,
                "intent": "set_goal",
                "data": goal,
                "message": (
                    f"🎯 **Savings Goal Set!**\n"
                    f"Target: **${amount:,.2f}** by end of {period}\n"
                    f"Period: {goal['start_date']} → {goal['end_date']}\n"
                    f"I'll track your income minus spending and keep you posted!"
                ),
            }

        # ── Goal progress check ──
        if any(kw in lower for kw in [
            "goal progress", "am i on track", "savings progress",
            "how much have i saved", "savings tracker",
        ]):
            progress = get_goal_progress()
            if not progress:
                return {
                    "skill": self.name,
                    "intent": "goal_progress",
                    "data": [],
                    "message": (
                        "No active savings goals. Set one with something like:\n"
                        "\"Set a savings goal of $500 this month\""
                    ),
                }
            msgs = [gp["message"] for gp in progress]
            return {
                "skill": self.name,
                "intent": "goal_progress",
                "data": progress,
                "message": "\n\n".join(msgs),
            }

        # ── Bill detection ──
        if any(kw in lower for kw in BILL_KEYWORDS):
            bills = detect_bills()
            if not bills:
                return {
                    "skill": self.name,
                    "intent": "bills",
                    "data": [],
                    "message": (
                        "I didn't find enough recurring transactions yet. "
                        "I need at least 2 months of data to detect billing patterns. "
                        "Check back soon!"
                    ),
                }
            lines = ["📋 **Detected Recurring Charges**\n"]
            total_monthly = 0.0
            for b in bills[:12]:
                freq_label = b["frequency"].title()
                conf_label = "●" * int(b["confidence"] * 5) + "○" * (5 - int(b["confidence"] * 5))
                next_str = f" → Next: {b['next_expected']}" if b["next_expected"] else ""
                lines.append(
                    f"- **{b['merchant']}**: ${b['avg_amount']:,.2f}/{freq_label} "
                    f"({b['occurrences']}x){next_str}"
                )
                if b["frequency"] == "monthly":
                    total_monthly += b["avg_amount"]
                elif b["frequency"] == "weekly":
                    total_monthly += b["avg_amount"] * 4.33
                elif b["frequency"] == "biweekly":
                    total_monthly += b["avg_amount"] * 2.17

            if total_monthly > 0:
                lines.append(f"\n💸 **Est. Monthly Recurring:** ${total_monthly:,.2f}")
            return {
                "skill": self.name,
                "intent": "bills",
                "data": bills,
                "message": "\n".join(lines),
            }

        # ── Spending alerts ──
        if any(kw in lower for kw in SPENDING_ALERT_KEYWORDS):
            alerts = check_spending_alerts()
            if not alerts:
                return {
                    "skill": self.name,
                    "intent": "spending_alerts",
                    "data": [],
                    "message": "✅ **All good!** No unusual spending spikes this month. You're staying consistent.",
                }
            lines = ["📊 **Spending Alerts**\n"]
            for a in alerts[:5]:
                lines.append(f"- {a['message']}")
            return {
                "skill": self.name,
                "intent": "spending_alerts",
                "data": alerts,
                "message": "\n".join(lines),
            }

        # ── Money personality ──
        if any(kw in lower for kw in PERSONALITY_KEYWORDS):
            personality = get_money_personality()
            if not personality.get("primary"):
                return {
                    "skill": self.name,
                    "intent": "personality",
                    "data": personality,
                    "message": personality["insights"][0] if personality["insights"] else "Not enough data yet.",
                }

            lines = ["🧠 **Your Money Personality**\n"]
            p = personality["primary"]
            lines.append(f"### {p['label']}")
            lines.append(f"*{_PERSONALITY_CATEGORIES[p['type']]['tagline']}*\n")

            for insight in personality["insights"]:
                lines.append(f"- {insight}")

            if personality["breakdown"]:
                lines.append("\n**Discretionary Spending Breakdown:**")
                for cat, pct in list(personality["breakdown"].items())[:6]:
                    lines.append(f"  - {cat}: {pct}%")

            return {
                "skill": self.name,
                "intent": "personality",
                "data": personality,
                "message": "\n".join(lines),
            }

        # ── Weekly report ──
        if any(kw in lower for kw in REPORT_KEYWORDS):
            report = generate_weekly_report()
            return {
                "skill": self.name,
                "intent": "weekly_report",
                "data": None,
                "message": report,
            }

        # ── Generic financial query — give a quick overview ──
        return {
            "skill": self.name,
            "intent": "general",
            "data": None,
            "message": (
                "I can help with your finances! Try asking me:\n"
                "• **\"Show my bills\"** — detect recurring charges\n"
                "• **\"Am I overspending?\"** — spending alerts\n"
                "• **\"Set a savings goal of $500\"** — track progress\n"
                "• **\"What's my money personality?\"** — spending insights\n"
                "• **\"Give me a financial report\"** — full weekly digest"
            ),
        }

    def get_prompt_context(self) -> str:
        """Inject financial intelligence context into AI system prompt."""
        if not is_bank_connected():
            return ""

        lines = ["💰 Financial Intelligence:"]
        has_content = False

        # Active goals
        goals = _load_goals()
        active_goals = [g for g in goals if g.get("active")]
        if active_goals:
            has_content = True
            lines.append("  🎯 Active Savings Goals:")
            for g in active_goals:
                lines.append(
                    f"    - {g.get('name', 'Goal')}: "
                    f"${g['target']:,.2f} by {g['end_date']}"
                )

        # Quick spending snapshot (use cached/recent data)
        try:
            transactions = _fetch_transactions(days=35)
            if transactions:
                has_content = True
                today = date.today()
                month_start = today.replace(day=1)
                month_expenses = sum(
                    t["amount"] for t in transactions
                    if t.get("amount", 0) > 0
                    and t.get("date", "").startswith(today.strftime("%Y-%m"))
                )
                lines.append(f"  📊 Month-to-date spending: ${month_expenses:,.2f}")

                # Upcoming bills
                bills = detect_bills(transactions=transactions, min_occurrences=2)
                upcoming = [
                    b for b in bills
                    if b.get("next_expected")
                    and b["confidence"] >= 0.5
                    and b["next_expected"] <= (today + timedelta(days=7)).isoformat()
                ]
                if upcoming:
                    lines.append("  📅 Bills due this week:")
                    for b in upcoming[:3]:
                        lines.append(
                            f"    - {b['merchant']}: ~${b['avg_amount']:,.2f} "
                            f"on {b['next_expected']}"
                        )
        except Exception as e:
            log.debug("Error building financial context: %s", e)

        if not has_content:
            return ""

        return "\n".join(lines)

    def get_proactive_nudges(self) -> list[str]:
        """Return actionable financial nudges."""
        nudges = []

        if not is_bank_connected():
            return nudges

        try:
            transactions = _fetch_transactions(days=62)
        except Exception:
            return nudges

        # Check spending alerts
        alerts = check_spending_alerts(transactions=transactions)
        for alert in alerts[:2]:
            nudges.append(alert["message"])

        # Check goal progress
        progress = get_goal_progress(transactions=transactions)
        for gp in progress:
            if not gp["on_track"]:
                nudges.append(gp["message"])

        # Upcoming bills in next 3 days
        today = date.today()
        bills = detect_bills(transactions=transactions)
        for b in bills:
            if (
                b.get("next_expected")
                and b["confidence"] >= 0.5
                and b["next_expected"] <= (today + timedelta(days=3)).isoformat()
            ):
                nudges.append(
                    f"📋 **{b['merchant']}** (~${b['avg_amount']:,.2f}) "
                    f"is expected to charge on {b['next_expected']}."
                )

        return nudges

    def get_morning_brief(self) -> str:
        """Financial section for morning brief."""
        if not is_bank_connected():
            return ""

        lines = []

        try:
            transactions = _fetch_transactions(days=62)
        except Exception:
            return ""

        if not transactions:
            return ""

        today = date.today()

        # Month-to-date spending
        month_expenses = sum(
            t["amount"] for t in transactions
            if t.get("amount", 0) > 0
            and t.get("date", "").startswith(today.strftime("%Y-%m"))
        )
        lines.append(f"💰 Month-to-date spending: ${month_expenses:,.2f}")

        # Bills due soon
        bills = detect_bills(transactions=transactions)
        upcoming = [
            b for b in bills
            if b.get("next_expected")
            and b["confidence"] >= 0.5
            and b["next_expected"] <= (today + timedelta(days=3)).isoformat()
        ]
        if upcoming:
            bill_names = ", ".join(
                f"{b['merchant']} (${b['avg_amount']:,.2f})" for b in upcoming[:3]
            )
            lines.append(f"📅 Bills due soon: {bill_names}")

        # Goal check
        progress = get_goal_progress(transactions=transactions)
        for gp in progress:
            status = "✅ on track" if gp["on_track"] else "⚠️ behind"
            lines.append(
                f"🎯 {gp['name']}: ${gp['saved']:,.2f}/${gp['target']:,.2f} ({status})"
            )

        # Spending alert count
        alerts = check_spending_alerts(transactions=transactions)
        if alerts:
            lines.append(f"⚠️ {len(alerts)} spending alert{'s' if len(alerts) != 1 else ''} this month")

        return "\n".join(lines) if lines else ""
