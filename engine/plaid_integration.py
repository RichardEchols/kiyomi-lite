"""Kiyomi × Plaid Integration — Real bank data for your AI assistant.

Handles:
- Plaid Link token creation (for connecting banks)
- Token exchange (public → access)
- Transaction fetching & categorization
- Balance checking
- Spending summaries & insights
- Secure token storage
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from collections import defaultdict

try:
    import plaid
    from plaid.api import plaid_api
    from plaid.model.link_token_create_request import LinkTokenCreateRequest
    from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
    from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
    from plaid.model.transactions_get_request import TransactionsGetRequest
    from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
    PLAID_AVAILABLE = True
except ImportError:
    PLAID_AVAILABLE = False

if PLAID_AVAILABLE:
    from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
    from plaid.model.products import Products
    from plaid.model.country_code import CountryCode

logger = logging.getLogger("kiyomi.plaid")

# ── Config ──────────────────────────────────────────────────

KIYOMI_DIR = Path.home() / ".kiyomi"
PLAID_CONFIG_FILE = KIYOMI_DIR / "plaid_tokens.json"


def _load_plaid_config() -> dict:
    """Load stored Plaid tokens and settings."""
    if PLAID_CONFIG_FILE.exists():
        return json.loads(PLAID_CONFIG_FILE.read_text())
    return {"items": [], "settings": {}}


def _save_plaid_config(config: dict):
    """Save Plaid tokens securely."""
    KIYOMI_DIR.mkdir(parents=True, exist_ok=True)
    PLAID_CONFIG_FILE.write_text(json.dumps(config, indent=2))
    # Restrict permissions
    PLAID_CONFIG_FILE.chmod(0o600)


def _get_client(client_id: str, secret: str, env: str = "sandbox"):
    """Create a Plaid API client."""
    if not PLAID_AVAILABLE:
        raise ImportError("Plaid SDK not installed. Run: pip install plaid-python")
    env_map = {
        "sandbox": plaid.Environment.Sandbox,
        "production": plaid.Environment.Production,
    }
    configuration = plaid.Configuration(
        host=env_map.get(env, plaid.Environment.Sandbox),
        api_key={
            "clientId": client_id,
            "secret": secret,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)


# ── Core Functions ──────────────────────────────────────────

def create_link_token(
    client_id: str,
    secret: str,
    env: str = "sandbox",
    user_id: str = "kiyomi-user",
    redirect_uri: Optional[str] = None,
) -> dict:
    """Create a Plaid Link token for connecting a bank account.
    
    Returns {"link_token": "...", "expiration": "..."} or {"error": "..."}
    """
    try:
        client = _get_client(client_id, secret, env)
        
        request = LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id=user_id),
            client_name="Kiyomi",
            products=[Products("transactions")],
            country_codes=[CountryCode("US")],
            language="en",
        )
        if redirect_uri:
            request.redirect_uri = redirect_uri
        
        response = client.link_token_create(request)
        return {
            "link_token": response.link_token,
            "expiration": str(response.expiration),
        }
    except Exception as e:
        logger.error(f"Failed to create link token: {e}")
        return {"error": str(e)}


def exchange_public_token(
    client_id: str,
    secret: str,
    public_token: str,
    env: str = "sandbox",
    institution_name: str = "Unknown Bank",
) -> dict:
    """Exchange a public token for an access token and store it.
    
    Returns {"access_token": "...", "item_id": "..."} or {"error": "..."}
    """
    try:
        client = _get_client(client_id, secret, env)
        
        request = ItemPublicTokenExchangeRequest(public_token=public_token)
        response = client.item_public_token_exchange(request)
        
        access_token = response.access_token
        item_id = response.item_id
        
        # Store token securely
        config = _load_plaid_config()
        config["items"].append({
            "access_token": access_token,
            "item_id": item_id,
            "institution": institution_name,
            "connected_at": datetime.now().isoformat(),
        })
        _save_plaid_config(config)
        
        return {
            "access_token": access_token,
            "item_id": item_id,
            "institution": institution_name,
        }
    except Exception as e:
        logger.error(f"Failed to exchange token: {e}")
        return {"error": str(e)}


def get_transactions(
    client_id: str,
    secret: str,
    env: str = "sandbox",
    days: int = 30,
    access_token: Optional[str] = None,
) -> dict:
    """Fetch transactions from connected bank accounts.
    
    If access_token is None, uses the first stored token.
    Returns {"transactions": [...], "accounts": [...]} or {"error": "..."}
    """
    try:
        if not access_token:
            config = _load_plaid_config()
            if not config["items"]:
                return {"error": "No bank accounts connected. Use /connect to link your bank."}
            access_token = config["items"][0]["access_token"]
        
        client = _get_client(client_id, secret, env)
        
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        
        request = TransactionsGetRequest(
            access_token=access_token,
            start_date=start_date,
            end_date=end_date,
            options=TransactionsGetRequestOptions(count=250, offset=0),
        )
        response = client.transactions_get(request)
        
        transactions = []
        for t in response.transactions:
            transactions.append({
                "name": t.name,
                "amount": float(t.amount),
                "date": str(t.date),
                "category": t.category[0] if t.category else "Other",
                "subcategory": t.category[1] if t.category and len(t.category) > 1 else None,
                "pending": t.pending,
                "merchant": t.merchant_name,
            })
        
        accounts = []
        for a in response.accounts:
            accounts.append({
                "name": a.name,
                "type": str(a.type),
                "subtype": str(a.subtype) if a.subtype else None,
                "balance": float(a.balances.current) if a.balances.current else 0,
                "available": float(a.balances.available) if a.balances.available else None,
                "mask": a.mask,
            })
        
        return {
            "transactions": transactions,
            "accounts": accounts,
            "total": response.total_transactions,
            "period": f"{start_date} to {end_date}",
        }
    except Exception as e:
        logger.error(f"Failed to get transactions: {e}")
        return {"error": str(e)}


def get_balances(
    client_id: str,
    secret: str,
    env: str = "sandbox",
    access_token: Optional[str] = None,
) -> dict:
    """Get current account balances.
    
    Returns {"accounts": [...], "total_balance": float} or {"error": "..."}
    """
    try:
        if not access_token:
            config = _load_plaid_config()
            if not config["items"]:
                return {"error": "No bank accounts connected. Use /connect to link your bank."}
            access_token = config["items"][0]["access_token"]
        
        client = _get_client(client_id, secret, env)
        
        request = AccountsBalanceGetRequest(access_token=access_token)
        response = client.accounts_balance_get(request)
        
        accounts = []
        total = 0.0
        for a in response.accounts:
            bal = float(a.balances.current) if a.balances.current else 0
            accounts.append({
                "name": a.name,
                "type": str(a.type),
                "balance": bal,
                "available": float(a.balances.available) if a.balances.available else None,
                "mask": a.mask,
            })
            if str(a.type) in ("depository", "investment"):
                total += bal
            elif str(a.type) == "credit":
                total -= bal  # Credit balances are owed
        
        return {"accounts": accounts, "net_worth": round(total, 2)}
    except Exception as e:
        logger.error(f"Failed to get balances: {e}")
        return {"error": str(e)}


# ── Smart Insights ──────────────────────────────────────────

def spending_summary(
    client_id: str,
    secret: str,
    env: str = "sandbox",
    days: int = 30,
) -> str:
    """Generate a natural-language spending summary.
    
    Returns a formatted string Kiyomi can send directly.
    """
    data = get_transactions(client_id, secret, env, days)
    if "error" in data:
        return data["error"]
    
    transactions = data["transactions"]
    if not transactions:
        return f"No transactions found in the last {days} days."
    
    # Group by category
    by_category: dict[str, float] = defaultdict(float)
    total_spent = 0.0
    total_income = 0.0
    
    for t in transactions:
        amount = t["amount"]
        if amount > 0:  # Plaid: positive = money out
            by_category[t["category"]] += amount
            total_spent += amount
        else:
            total_income += abs(amount)
    
    # Sort categories by spend
    sorted_cats = sorted(by_category.items(), key=lambda x: x[1], reverse=True)
    
    # Build summary
    period = f"last {days} days" if days != 7 else "this week"
    lines = [f"💰 **Spending Summary ({period})**\n"]
    lines.append(f"**Total Spent:** ${total_spent:,.2f}")
    if total_income > 0:
        lines.append(f"**Total Income:** ${total_income:,.2f}")
        net = total_income - total_spent
        emoji = "📈" if net > 0 else "📉"
        lines.append(f"**Net:** {emoji} ${net:,.2f}")
    
    lines.append(f"\n**By Category:**")
    for cat, amount in sorted_cats[:10]:
        pct = (amount / total_spent * 100) if total_spent > 0 else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        lines.append(f"  {cat}: ${amount:,.2f} ({pct:.0f}%)")
    
    # Top merchants
    by_merchant: dict[str, float] = defaultdict(float)
    for t in transactions:
        if t["amount"] > 0 and t["merchant"]:
            by_merchant[t["merchant"]] += t["amount"]
    
    if by_merchant:
        top_merchants = sorted(by_merchant.items(), key=lambda x: x[1], reverse=True)[:5]
        lines.append(f"\n**Top Merchants:**")
        for name, amount in top_merchants:
            lines.append(f"  🏪 {name}: ${amount:,.2f}")
    
    return "\n".join(lines)


def balance_summary(
    client_id: str,
    secret: str,
    env: str = "sandbox",
) -> str:
    """Generate a natural-language balance summary."""
    data = get_balances(client_id, secret, env)
    if "error" in data:
        return data["error"]
    
    lines = [f"🏦 **Account Balances**\n"]
    
    for acct in data["accounts"]:
        emoji = {"depository": "💵", "credit": "💳", "investment": "📊", "loan": "🏠"}.get(acct["type"], "🏦")
        mask = f" (•••{acct['mask']})" if acct["mask"] else ""
        lines.append(f"{emoji} **{acct['name']}**{mask}")
        lines.append(f"   Balance: ${acct['balance']:,.2f}")
        if acct["available"] is not None and acct["available"] != acct["balance"]:
            lines.append(f"   Available: ${acct['available']:,.2f}")
    
    lines.append(f"\n**Net Worth:** ${data['net_worth']:,.2f}")
    
    return "\n".join(lines)


def category_spending(
    client_id: str,
    secret: str,
    env: str = "sandbox",
    category: str = "Food and Drink",
    days: int = 30,
) -> str:
    """Get spending for a specific category."""
    data = get_transactions(client_id, secret, env, days)
    if "error" in data:
        return data["error"]
    
    cat_lower = category.lower()
    matching = [
        t for t in data["transactions"]
        if t["amount"] > 0 and (
            cat_lower in (t["category"] or "").lower()
            or cat_lower in (t["subcategory"] or "").lower()
            or cat_lower in (t["name"] or "").lower()
            or cat_lower in (t["merchant"] or "").lower()
        )
    ]
    
    if not matching:
        return f"No spending found for '{category}' in the last {days} days."
    
    total = sum(t["amount"] for t in matching)
    lines = [f"🔍 **{category} Spending (last {days} days)**\n"]
    lines.append(f"**Total:** ${total:,.2f} across {len(matching)} transactions\n")
    
    for t in sorted(matching, key=lambda x: x["date"], reverse=True)[:10]:
        merchant = t["merchant"] or t["name"]
        lines.append(f"  • {t['date']} — {merchant}: ${t['amount']:.2f}")
    
    if len(matching) > 10:
        lines.append(f"  ... and {len(matching) - 10} more")
    
    return "\n".join(lines)


# ── Connection Management ───────────────────────────────────

def get_connected_banks() -> list[dict]:
    """List all connected bank accounts."""
    config = _load_plaid_config()
    return [
        {
            "institution": item["institution"],
            "connected_at": item.get("connected_at", "Unknown"),
        }
        for item in config["items"]
    ]


def is_bank_connected() -> bool:
    """Check if any bank is connected."""
    config = _load_plaid_config()
    return len(config["items"]) > 0


def disconnect_bank(index: int = 0) -> str:
    """Remove a connected bank account."""
    config = _load_plaid_config()
    if not config["items"]:
        return "No bank accounts connected."
    if index >= len(config["items"]):
        return f"Invalid bank index. You have {len(config['items'])} connected."
    
    removed = config["items"].pop(index)
    _save_plaid_config(config)
    return f"Disconnected {removed['institution']}."
