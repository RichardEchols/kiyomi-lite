"""
Kiyomi Lite — Business Features Skill
Professional business management for entrepreneurs, freelancers, and small business owners.

Core Features:
1. Client Memory — Store and recall client details, preferences, history
2. Deadline Guardian — Track deadlines with escalating urgency alerts
3. Instant Document Drafts — Pre-fill templates with client/user data
4. Daily Revenue Report — Track income with daily/weekly/monthly totals
5. Client Follow-Up Automation — Track last contact, auto-suggest follow-ups
6. Vendor/Contact Rolodex — Quick business contact lookup
7. Meeting Prep — Compile relevant notes and history
8. Tax Time Helper — Revenue summary and tax preparation assistance
"""

import json
import logging
import re
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

try:
    from skills.base import Skill
except ImportError:
    from engine.skills.base import Skill

logger = logging.getLogger("kiyomi.skills.business")

# Storage paths
BUSINESS_DIR = Path.home() / ".kiyomi" / "skills" / "business"
CLIENTS_FILE = BUSINESS_DIR / "clients.json"
DEADLINES_FILE = BUSINESS_DIR / "deadlines.json"
REVENUE_FILE = BUSINESS_DIR / "revenue.json"
CONTACTS_FILE = BUSINESS_DIR / "contacts.json"


class BusinessSkill(Skill):
    """Professional business management and client relationship tracking."""

    name = "business"
    description = "Client memory, deadlines, revenue tracking, document drafts, and business automation"

    # Intent patterns for detecting business-related messages
    INTENT_PATTERNS = [
        # Client memory
        r"remember (?:about\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:prefers?|likes?|wants?|needs?)\s+(.+)",
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:prefers?|likes?)\s+(.+)",
        r"client\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(.+)",
        r"(?:mrs?\.?\s+|ms\.?\s+)?([A-Z][a-z]+)\s+(?:email|phone|contact|address)(?:\s+is)?\s+(.+)",
        
        # Deadlines
        r"(?:deadline|due date|filing|submission)\s+(?:is\s+)?(?:on\s+)?(\w+\s+\d+)",
        r"need to (?:file|submit|finish)\s+(.+?)\s+by\s+(\w+\s+\d+)",
        r"(.+?)\s+deadline\s+(?:is\s+)?(?:on\s+)?(\w+\s+\d+)",
        
        # Revenue tracking
        r"(?:got paid|received|earned|made)\s+\$?([\d,]+(?:\.\d{2})?)",
        r"invoice\s+(?:paid|received)\s+\$?([\d,]+(?:\.\d{2})?)",
        r"payment\s+(?:from\s+)?(.+?)\s+\$?([\d,]+(?:\.\d{2})?)",
        
        # Contact/follow-up
        r"(?:called|contacted|met with|emailed)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"(?:need to|should)\s+(?:call|contact|follow up with)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        
        # Vendor/contacts
        r"save\s+(?:contact|vendor)\s*:\s*(.+?)\s+([\d\-\(\)\s]+)",
        r"(?:my\s+)?(\w+(?:\s+\w+)*)\s+(?:is|contact)\s+(.+?)\s+([\d\-\(\)\s]+)",
        
        # Meeting prep
        r"prep(?:\s+me)?\s+for\s+(?:meeting|call)\s+(?:with\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"meeting\s+(?:with\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:today|tomorrow|this\s+week)",
        
        # Document drafts
        r"draft\s+(?:a\s+)?(.+?)\s+(?:to|for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
    ]

    KEYWORDS = [
        # Client keywords
        "client", "customer", "mrs", "mr", "ms", "prefers", "likes", "contact",
        # Deadline keywords
        "deadline", "due", "filing", "submission", "finish", "complete", "urgent",
        # Revenue keywords
        "paid", "payment", "invoice", "earned", "revenue", "income", "billing",
        # Contact keywords
        "called", "contacted", "follow up", "reach out", "email", "phone", "meeting",
        # Vendor keywords
        "vendor", "supplier", "contractor", "service provider", "plumber", "accountant",
        # Business documents
        "draft", "letter", "proposal", "contract", "quote", "estimate", "invoice",
        # Business commands
        "/revenue", "/tax", "tax time", "tax prep", "prep me", "meeting prep"
    ]

    def __init__(self):
        super().__init__()
        BUSINESS_DIR.mkdir(parents=True, exist_ok=True)

    def detect(self, message: str) -> bool:
        """Does this message relate to business features?"""
        msg_lower = message.lower()
        
        # Check for keywords first
        if any(kw in msg_lower for kw in self.KEYWORDS):
            return True
            
        # Check for intent patterns
        for pattern in self.INTENT_PATTERNS:
            if re.search(pattern, message, re.IGNORECASE):
                return True
                
        return False

    def extract(self, message: str, response: str) -> dict | None:
        """Pull structured data from conversation."""
        return self.process_message(message, response)

    def get_prompt_context(self) -> str:
        """Return context string to inject into AI system prompt."""
        context_parts = []
        
        # Recent clients
        clients = self._load_clients()
        if clients:
            recent_clients = sorted(
                clients.items(), 
                key=lambda x: x[1].get('last_updated', ''), 
                reverse=True
            )[:5]
            client_names = [name for name, _ in recent_clients]
            context_parts.append(f"Recent clients: {', '.join(client_names)}")

        # Upcoming deadlines
        upcoming = self._get_upcoming_deadlines(7)
        if upcoming:
            deadline_list = [f"{d['task']} ({d['date']})" for d in upcoming[:3]]
            context_parts.append(f"Upcoming deadlines: {', '.join(deadline_list)}")

        # Revenue summary (this month)
        revenue_data = self._load_revenue()
        month_key = date.today().strftime("%Y-%m")
        month_revenue = sum(
            entry['amount'] for entry in revenue_data.get('entries', [])
            if entry.get('date', '').startswith(month_key)
        )
        if month_revenue > 0:
            context_parts.append(f"Revenue this month: ${month_revenue:,.2f}")

        if not context_parts:
            return ""
            
        return "Business: " + " | ".join(context_parts)

    def get_proactive_nudges(self) -> list[str]:
        """Return proactive business reminders."""
        nudges = []
        
        # Check for urgent deadlines
        urgent = self._get_upcoming_deadlines(1)  # Today and overdue
        for deadline in urgent:
            if deadline['days_until'] < 0:
                nudges.append(f"🚨 OVERDUE: {deadline['task']} was due {deadline['date']}!")
            elif deadline['days_until'] == 0:
                nudges.append(f"📅 DUE TODAY: {deadline['task']}")

        # Check for follow-up suggestions
        follow_ups = self._get_follow_up_suggestions()
        for client_name, days_ago in follow_ups[:2]:  # Limit to 2 suggestions
            nudges.append(f"💼 Consider following up with {client_name} (last contact {days_ago} days ago)")

        return nudges

    def get_morning_brief(self) -> str:
        """Business section for morning brief."""
        lines = []
        
        # Today's deadlines
        today_deadlines = self._get_upcoming_deadlines(0)  # Only today
        if today_deadlines:
            deadline_list = [d['task'] for d in today_deadlines]
            lines.append(f"📅 Deadlines today: {', '.join(deadline_list)}")

        # Follow-ups needed
        follow_ups = self._get_follow_up_suggestions()
        if follow_ups:
            lines.append(f"💼 {len(follow_ups)} clients need follow-up")

        # Weekly revenue update (if it's Monday)
        if datetime.now().weekday() == 0:  # Monday
            week_revenue = self._get_weekly_revenue()
            if week_revenue > 0:
                lines.append(f"💰 Last week's revenue: ${week_revenue:,.2f}")

        return "\n".join(lines) if lines else ""

    def process_message(self, user_msg: str, ai_response: str) -> dict | None:
        """Main message processing router."""
        msg_lower = user_msg.lower()
        
        # Handle commands first
        if user_msg.startswith('/revenue'):
            return self.handle_command('revenue', user_msg)
        elif user_msg.startswith('/tax'):
            return self.handle_command('tax', user_msg)

        # 1. Client memory extraction
        client_data = self._extract_client_info(user_msg)
        if client_data:
            self._save_client_info(**client_data)
            logger.info(f"Saved client info: {client_data['name']}")

        # 2. Deadline extraction
        deadline_data = self._extract_deadline(user_msg)
        if deadline_data:
            self._save_deadline(**deadline_data)
            logger.info(f"Saved deadline: {deadline_data['task']}")

        # 3. Revenue extraction
        revenue_data = self._extract_revenue(user_msg)
        if revenue_data:
            self._save_revenue(**revenue_data)
            logger.info(f"Saved revenue: ${revenue_data['amount']}")

        # 4. Contact extraction
        contact_data = self._extract_contact_activity(user_msg)
        if contact_data:
            self._update_last_contact(**contact_data)
            logger.info(f"Updated contact: {contact_data['client_name']}")

        # 5. Vendor/contact extraction
        vendor_data = self._extract_vendor_contact(user_msg)
        if vendor_data:
            self._save_vendor_contact(**vendor_data)
            logger.info(f"Saved vendor: {vendor_data['name']}")

        # 6. Meeting prep request
        prep_request = self._extract_meeting_prep_request(user_msg)
        if prep_request:
            return self._handle_meeting_prep(prep_request['client_name'])

        # 7. Document draft request
        draft_request = self._extract_document_draft_request(user_msg)
        if draft_request:
            return self._handle_document_draft(**draft_request)

        # 8. General business query handling
        business_query = self._extract_business_query(user_msg)
        if business_query:
            return self._handle_business_query(business_query)

        return None

    def handle_command(self, command: str, message: str) -> dict:
        """Handle business commands like /revenue, /tax."""
        if command == 'revenue':
            return self._handle_revenue_command(message)
        elif command == 'tax':
            return self._handle_tax_command(message)
        return {"message": "Unknown business command"}

    # ═══ CLIENT MEMORY ═══════════════════════════════════════
    
    def _load_clients(self) -> dict:
        """Load client data from JSON file."""
        if CLIENTS_FILE.exists():
            try:
                return json.loads(CLIENTS_FILE.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_clients(self, clients: dict):
        """Save client data to JSON file."""
        CLIENTS_FILE.write_text(json.dumps(clients, indent=2, default=str))

    def _extract_client_info(self, message: str) -> Optional[dict]:
        """Extract client information from message."""
        patterns = [
            r"remember (?:about\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:prefers?|likes?|wants?|needs?)\s+(.+)",
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:prefers?|likes?)\s+(.+)",
            r"(?:mrs?\.?\s+|ms\.?\s+)?([A-Z][a-z]+)\s+(?:email|phone|contact|address)(?:\s+is)?\s+(.+)",
            r"client\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(.+)"
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                info = match.group(2).strip()
                
                # Determine info type
                info_type = "preference"
                if any(contact_word in info.lower() for contact_word in ["@", "phone", "email", "address"]):
                    if "@" in info:
                        info_type = "email"
                    elif any(char.isdigit() for char in info):
                        info_type = "phone"
                    else:
                        info_type = "address"
                
                return {
                    "name": name,
                    "info_type": info_type,
                    "info": info
                }
        return None

    def _save_client_info(self, name: str, info_type: str, info: str):
        """Save client information."""
        clients = self._load_clients()
        
        if name not in clients:
            clients[name] = {
                "name": name,
                "preferences": [],
                "contact_info": {},
                "history": [],
                "last_updated": datetime.now().isoformat(),
                "created": datetime.now().isoformat()
            }

        client = clients[name]
        
        if info_type == "preference":
            if info not in client["preferences"]:
                client["preferences"].append(info)
        elif info_type in ["email", "phone", "address"]:
            client["contact_info"][info_type] = info
        else:
            client["history"].append({
                "date": datetime.now().isoformat(),
                "note": info
            })

        client["last_updated"] = datetime.now().isoformat()
        clients[name] = client
        
        self._save_clients(clients)

    def _get_client_info(self, name: str) -> Optional[dict]:
        """Retrieve client information by name."""
        clients = self._load_clients()
        # Try exact match first
        if name in clients:
            return clients[name]
        # Try case-insensitive match
        for client_name, client_data in clients.items():
            if client_name.lower() == name.lower():
                return client_data
        return None

    # ═══ DEADLINE GUARDIAN ═══════════════════════════════════

    def _load_deadlines(self) -> list:
        """Load deadlines from JSON file."""
        if DEADLINES_FILE.exists():
            try:
                return json.loads(DEADLINES_FILE.read_text())
            except json.JSONDecodeError:
                return []
        return []

    def _save_deadlines(self, deadlines: list):
        """Save deadlines to JSON file."""
        DEADLINES_FILE.write_text(json.dumps(deadlines, indent=2, default=str))

    def _extract_deadline(self, message: str) -> Optional[dict]:
        """Extract deadline information from message."""
        patterns = [
            r"(?:deadline|due date|filing|submission)\s+(?:is\s+)?(?:on\s+)?(\w+\s+\d+)",
            r"need to (?:file|submit|finish)\s+(.+?)\s+by\s+(\w+\s+\d+)",
            r"(.+?)\s+deadline\s+(?:is\s+)?(?:on\s+)?(\w+\s+\d+)"
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                if len(match.groups()) == 1:
                    # Pattern 1: deadline is March 15
                    task = "Important deadline"
                    date_str = match.group(1)
                else:
                    # Patterns 2-3: task by date or task deadline date
                    task = match.group(1).strip()
                    date_str = match.group(2)
                
                return {
                    "task": task,
                    "date_str": date_str,
                    "message": message
                }
        return None

    def _save_deadline(self, task: str, date_str: str, message: str):
        """Save a new deadline."""
        deadlines = self._load_deadlines()
        
        # Parse date
        parsed_date = self._parse_date(date_str)
        if not parsed_date:
            return
            
        deadline = {
            "id": len(deadlines) + 1,
            "task": task,
            "date": parsed_date.isoformat(),
            "date_str": date_str,
            "created": datetime.now().isoformat(),
            "completed": False,
            "original_message": message
        }
        
        deadlines.append(deadline)
        self._save_deadlines(deadlines)

    def _parse_date(self, date_str: str) -> Optional[date]:
        """Parse various date formats."""
        import calendar
        
        current_year = date.today().year
        
        # Try parsing "Month Day" format
        for fmt in ["%B %d", "%b %d"]:
            try:
                parsed = datetime.strptime(f"{date_str} {current_year}", f"{fmt} %Y")
                result_date = parsed.date()
                # If date is in the past, assume next year
                if result_date < date.today():
                    result_date = result_date.replace(year=current_year + 1)
                return result_date
            except ValueError:
                continue
        
        return None

    def _get_upcoming_deadlines(self, days_ahead: int = 7) -> list[dict]:
        """Get deadlines in the next N days (0 = today only, negative = overdue)."""
        deadlines = self._load_deadlines()
        upcoming = []
        today = date.today()
        cutoff = today + timedelta(days=days_ahead) if days_ahead > 0 else today
        
        for deadline in deadlines:
            if deadline.get('completed'):
                continue
                
            try:
                deadline_date = datetime.fromisoformat(deadline['date']).date()
                days_until = (deadline_date - today).days
                
                if days_ahead == 0:  # Today only
                    if days_until == 0:
                        upcoming.append({**deadline, 'days_until': days_until})
                elif days_ahead > 0:  # Next N days
                    if deadline_date <= cutoff:
                        upcoming.append({**deadline, 'days_until': days_until})
                else:  # All upcoming
                    upcoming.append({**deadline, 'days_until': days_until})
                        
            except (ValueError, KeyError):
                continue
        
        return sorted(upcoming, key=lambda x: x['days_until'])

    # ═══ REVENUE TRACKING ════════════════════════════════════

    def _load_revenue(self) -> dict:
        """Load revenue data from JSON file."""
        if REVENUE_FILE.exists():
            try:
                return json.loads(REVENUE_FILE.read_text())
            except json.JSONDecodeError:
                return {"entries": []}
        return {"entries": []}

    def _save_revenue(self, **revenue_data):
        """Save revenue data."""
        data = self._load_revenue()
        
        entry = {
            "date": date.today().isoformat(),
            "amount": revenue_data['amount'],
            "source": revenue_data.get('source', 'Unknown'),
            "description": revenue_data.get('description', ''),
            "created": datetime.now().isoformat(),
            "id": len(data['entries']) + 1
        }
        
        data['entries'].append(entry)
        REVENUE_FILE.write_text(json.dumps(data, indent=2, default=str))

    def _extract_revenue(self, message: str) -> Optional[dict]:
        """Extract revenue information from message."""
        patterns = [
            r"(?:got paid|received|earned|made)\s+\$?([\d,]+(?:\.\d{2})?)",
            r"invoice\s+(?:paid|received)\s+\$?([\d,]+(?:\.\d{2})?)",
            r"payment\s+(?:from\s+)?(.+?)\s+\$?([\d,]+(?:\.\d{2})?)"
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                if len(match.groups()) == 1:
                    # Pattern 1-2: got paid $5000
                    amount_str = match.group(1)
                    source = "Unknown"
                else:
                    # Pattern 3: payment from Smith case $5000
                    source = match.group(1).strip()
                    amount_str = match.group(2)
                
                try:
                    amount = float(amount_str.replace(',', ''))
                    return {
                        "amount": amount,
                        "source": source,
                        "description": message
                    }
                except ValueError:
                    continue
        return None

    def _handle_revenue_command(self, message: str) -> dict:
        """Handle /revenue command - show revenue report."""
        data = self._load_revenue()
        entries = data.get('entries', [])
        
        if not entries:
            return {"message": "No revenue entries recorded yet."}

        today = date.today()
        
        # Daily total (today)
        daily_total = sum(
            entry['amount'] for entry in entries 
            if entry.get('date') == today.isoformat()
        )
        
        # Weekly total (last 7 days)
        week_ago = today - timedelta(days=7)
        weekly_total = sum(
            entry['amount'] for entry in entries
            if datetime.fromisoformat(entry.get('date', '')).date() >= week_ago
        )
        
        # Monthly total (this month)
        month_key = today.strftime("%Y-%m")
        monthly_total = sum(
            entry['amount'] for entry in entries
            if entry.get('date', '').startswith(month_key)
        )

        # Recent entries
        recent_entries = sorted(entries, key=lambda x: x.get('date', ''), reverse=True)[:5]

        lines = [
            "💰 **Revenue Report**",
            f"Today: ${daily_total:,.2f}",
            f"Last 7 days: ${weekly_total:,.2f}",
            f"This month: ${monthly_total:,.2f}",
            "",
            "**Recent Entries:**"
        ]
        
        for entry in recent_entries:
            lines.append(f"• ${entry['amount']:,.2f} from {entry['source']} ({entry['date']})")

        return {"message": "\n".join(lines)}

    def _get_weekly_revenue(self) -> float:
        """Get revenue from last 7 days."""
        data = self._load_revenue()
        entries = data.get('entries', [])
        week_ago = date.today() - timedelta(days=7)
        
        return sum(
            entry['amount'] for entry in entries
            if datetime.fromisoformat(entry.get('date', '')).date() >= week_ago
        )

    # ═══ CLIENT FOLLOW-UP AUTOMATION ══════════════════════════

    def _extract_contact_activity(self, message: str) -> Optional[dict]:
        """Extract client contact activity."""
        patterns = [
            r"(?:called|contacted|met with|emailed|spoke with)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"(?:call|meeting|email|conversation)\s+(?:with\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:today|yesterday)"
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return {
                    "client_name": match.group(1).strip(),
                    "activity": "contact",
                    "date": date.today().isoformat()
                }
        return None

    def _update_last_contact(self, client_name: str, activity: str, contact_date: str):
        """Update last contact date for a client."""
        clients = self._load_clients()
        
        if client_name not in clients:
            # Create new client record
            clients[client_name] = {
                "name": client_name,
                "preferences": [],
                "contact_info": {},
                "history": [],
                "created": datetime.now().isoformat()
            }

        clients[client_name]["last_contact"] = contact_date
        clients[client_name]["last_updated"] = datetime.now().isoformat()
        
        # Add to history
        clients[client_name]["history"].append({
            "date": contact_date,
            "activity": activity,
            "note": f"Contact recorded from conversation"
        })

        self._save_clients(clients)

    def _get_follow_up_suggestions(self, days_threshold: int = 14) -> list[tuple[str, int]]:
        """Get clients who need follow-up (haven't been contacted in X days)."""
        clients = self._load_clients()
        suggestions = []
        today = date.today()
        
        for name, client in clients.items():
            last_contact_str = client.get('last_contact')
            if not last_contact_str:
                continue
                
            try:
                last_contact = datetime.fromisoformat(last_contact_str).date()
                days_ago = (today - last_contact).days
                
                if days_ago >= days_threshold:
                    suggestions.append((name, days_ago))
            except ValueError:
                continue
        
        return sorted(suggestions, key=lambda x: x[1], reverse=True)

    # ═══ VENDOR/CONTACT ROLODEX ════════════════════════════════

    def _load_contacts(self) -> dict:
        """Load vendor/business contacts."""
        if CONTACTS_FILE.exists():
            try:
                return json.loads(CONTACTS_FILE.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_contacts(self, contacts: dict):
        """Save vendor/business contacts."""
        CONTACTS_FILE.write_text(json.dumps(contacts, indent=2, default=str))

    def _extract_vendor_contact(self, message: str) -> Optional[dict]:
        """Extract vendor/contact information."""
        patterns = [
            r"save\s+(?:contact|vendor)\s*:\s*(.+?)\s+([\d\-\(\)\s]+)",
            r"(?:my\s+)?(\w+(?:\s+\w+)*)\s+(?:is|contact)\s+(.+?)\s+([\d\-\(\)\s]+)"
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                if "save" in message.lower():
                    name = match.group(1).strip()
                    phone = match.group(2).strip()
                    return {
                        "name": name,
                        "phone": phone,
                        "type": "vendor"
                    }
                else:
                    service_type = match.group(1).strip()
                    name = match.group(2).strip()
                    phone = match.group(3).strip()
                    return {
                        "name": name,
                        "phone": phone,
                        "type": service_type
                    }
        return None

    def _save_vendor_contact(self, name: str, phone: str, contact_type: str):
        """Save a vendor contact."""
        contacts = self._load_contacts()
        
        contact = {
            "name": name,
            "phone": phone,
            "type": contact_type,
            "created": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat()
        }
        
        # Use name as key (case-insensitive)
        key = name.lower().replace(" ", "_")
        contacts[key] = contact
        
        self._save_contacts(contacts)

    # ═══ MEETING PREP ═══════════════════════════════════════

    def _extract_meeting_prep_request(self, message: str) -> Optional[dict]:
        """Extract meeting prep requests."""
        patterns = [
            r"prep(?:\s+me)?\s+for\s+(?:meeting|call)\s+(?:with\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"meeting\s+(?:with\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:today|tomorrow|prep)"
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return {
                    "client_name": match.group(1).strip()
                }
        return None

    def _handle_meeting_prep(self, client_name: str) -> dict:
        """Compile meeting preparation information."""
        lines = [f"📋 **Meeting Prep: {client_name}**", ""]
        
        # Client information
        client = self._get_client_info(client_name)
        if client:
            lines.append("**Client Info:**")
            if client.get('contact_info'):
                for info_type, info_value in client['contact_info'].items():
                    lines.append(f"• {info_type.title()}: {info_value}")
            
            if client.get('preferences'):
                lines.append(f"• Preferences: {', '.join(client['preferences'])}")
            
            if client.get('last_contact'):
                last_contact_date = client['last_contact']
                lines.append(f"• Last contact: {last_contact_date}")
            
            lines.append("")
            
            # Recent history
            if client.get('history'):
                lines.append("**Recent History:**")
                recent_history = sorted(client['history'], key=lambda x: x.get('date', ''), reverse=True)[:3]
                for item in recent_history:
                    lines.append(f"• {item['date']}: {item.get('note', item.get('activity', 'Contact'))}")
                lines.append("")

        # Pending deadlines related to this client
        deadlines = self._load_deadlines()
        client_deadlines = [
            d for d in deadlines 
            if not d.get('completed') and client_name.lower() in d.get('task', '').lower()
        ]
        if client_deadlines:
            lines.append("**Related Deadlines:**")
            for deadline in client_deadlines[:3]:
                lines.append(f"• {deadline['task']} - Due: {deadline.get('date_str', deadline['date'])}")
            lines.append("")

        if len(lines) == 2:  # Only header was added
            lines.append("No information found for this client yet.")

        return {"message": "\n".join(lines)}

    # ═══ INSTANT DOCUMENT DRAFTS ═══════════════════════════════

    def _extract_document_draft_request(self, message: str) -> Optional[dict]:
        """Extract document draft requests."""
        patterns = [
            r"draft\s+(?:a\s+)?(.+?)\s+(?:to|for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)"
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return {
                    "document_type": match.group(1).strip(),
                    "client_name": match.group(2).strip()
                }
        return None

    def _handle_document_draft(self, document_type: str, client_name: str) -> dict:
        """Generate a document draft with client information."""
        client = self._get_client_info(client_name)
        
        # Get user info (from memory if available)
        user_name = "Your Name"  # TODO: Get from memory
        user_email = "your.email@example.com"  # TODO: Get from memory
        
        # Template based on document type
        if "email" in document_type.lower() or "follow" in document_type.lower():
            template = self._generate_email_template(client, user_name, user_email)
        elif "proposal" in document_type.lower() or "quote" in document_type.lower():
            template = self._generate_proposal_template(client, user_name)
        elif "letter" in document_type.lower():
            template = self._generate_letter_template(client, user_name)
        else:
            template = self._generate_generic_template(client, user_name, document_type)

        return {"message": template}

    def _generate_email_template(self, client: Optional[dict], user_name: str, user_email: str) -> str:
        """Generate email template."""
        client_email = ""
        client_name = "Client"
        
        if client:
            client_name = client['name']
            client_email = client.get('contact_info', {}).get('email', '[CLIENT_EMAIL]')
            
        template = f"""📧 **Email Draft**

**To:** {client_email}
**From:** {user_email}
**Subject:** [SUBJECT LINE]

Dear {client_name},

I hope this email finds you well. 

[MAIN MESSAGE CONTENT]

"""

        if client and client.get('preferences'):
            template += f"*Note: Remember {client_name} prefers: {', '.join(client['preferences'])}*\n\n"

        template += f"""Please let me know if you have any questions.

Best regards,
{user_name}
"""
        
        return template

    def _generate_proposal_template(self, client: Optional[dict], user_name: str) -> str:
        """Generate proposal template."""
        client_name = client['name'] if client else "[CLIENT_NAME]"
        
        return f"""📄 **Proposal Draft**

# Proposal for {client_name}

**Date:** {date.today().strftime('%B %d, %Y')}
**Prepared by:** {user_name}

## Project Overview
[DESCRIBE THE PROJECT/SERVICE]

## Scope of Work
- [DELIVERABLE 1]
- [DELIVERABLE 2]
- [DELIVERABLE 3]

## Timeline
- **Start Date:** [DATE]
- **Completion Date:** [DATE]

## Investment
**Total Project Cost:** $[AMOUNT]

## Next Steps
[DESCRIBE NEXT STEPS]

---
*Proposal valid for 30 days*
"""

    def _generate_letter_template(self, client: Optional[dict], user_name: str) -> str:
        """Generate business letter template."""
        client_name = client['name'] if client else "[CLIENT_NAME]"
        today_str = date.today().strftime('%B %d, %Y')
        
        return f"""📝 **Business Letter Draft**

{today_str}

{client_name}
[CLIENT_ADDRESS]

Dear {client_name},

[LETTER CONTENT]

Sincerely,

{user_name}
[YOUR_TITLE]
[YOUR_COMPANY]
"""

    def _generate_generic_template(self, client: Optional[dict], user_name: str, doc_type: str) -> str:
        """Generate generic document template."""
        client_name = client['name'] if client else "[CLIENT_NAME]"
        
        return f"""📄 **{doc_type.title()} Draft**

**Date:** {date.today().strftime('%B %d, %Y')}
**Client:** {client_name}
**Prepared by:** {user_name}

[DOCUMENT CONTENT]

---
*Document prepared for {client_name}*
"""

    # ═══ TAX TIME HELPER ═════════════════════════════════════

    def _handle_tax_command(self, message: str) -> dict:
        """Handle /tax command - generate tax preparation summary."""
        revenue_data = self._load_revenue()
        entries = revenue_data.get('entries', [])
        
        if not entries:
            return {"message": "No revenue data recorded for tax preparation."}

        # Calculate yearly totals (current year)
        current_year = date.today().year
        yearly_entries = [
            entry for entry in entries
            if entry.get('date', '').startswith(str(current_year))
        ]
        
        if not yearly_entries:
            return {"message": f"No revenue recorded for {current_year} yet."}

        total_revenue = sum(entry['amount'] for entry in yearly_entries)
        
        # Group by source
        by_source = {}
        for entry in yearly_entries:
            source = entry.get('source', 'Unknown')
            if source not in by_source:
                by_source[source] = 0
            by_source[source] += entry['amount']

        # Group by month
        by_month = {}
        for entry in yearly_entries:
            month = entry.get('date', '')[:7]  # YYYY-MM
            if month not in by_month:
                by_month[month] = 0
            by_month[month] += entry['amount']

        lines = [
            f"📊 **Tax Preparation Summary - {current_year}**",
            "",
            f"**Total Revenue:** ${total_revenue:,.2f}",
            "",
            "**Revenue by Source:**"
        ]
        
        for source, amount in sorted(by_source.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"• {source}: ${amount:,.2f}")
        
        lines.extend([
            "",
            "**Monthly Breakdown:**"
        ])
        
        for month, amount in sorted(by_month.items()):
            month_name = datetime.strptime(month, '%Y-%m').strftime('%B %Y')
            lines.append(f"• {month_name}: ${amount:,.2f}")
        
        lines.extend([
            "",
            "**Tax Notes:**",
            "• Save all receipts for business expenses",
            "• Consider quarterly estimated payments",
            "• Consult with accountant for deductions",
            "",
            f"*Summary generated on {date.today().strftime('%B %d, %Y')}*"
        ])

        return {"message": "\n".join(lines)}

    # ═══ BUSINESS QUERY HANDLING ══════════════════════════════

    def _extract_business_query(self, message: str) -> Optional[str]:
        """Extract general business queries."""
        query_patterns = [
            r"what (?:do i know|info do i have) about\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"(?:who\'s|who is)\s+my\s+(\w+)",
            r"when (?:was|did) (?:i last|last)\s+(?:contact|call|email)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"(?:upcoming|pending)\s+deadlines?",
            r"revenue\s+(?:summary|report|total)",
            r"follow\s*up\s+(?:list|suggestions)"
        ]

        for pattern in query_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return message
        
        return None

    def _handle_business_query(self, query: str) -> dict:
        """Handle general business information queries."""
        query_lower = query.lower()
        
        # Client information lookup
        if "what" in query_lower and "know about" in query_lower:
            # Extract client name
            match = re.search(r"what (?:do i know|info do i have) about\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", query, re.IGNORECASE)
            if match:
                client_name = match.group(1).strip()
                return self._handle_client_lookup(client_name)
        
        # Contact lookup
        if "who" in query_lower and ("my" in query_lower or "is" in query_lower):
            match = re.search(r"(?:who\'s|who is)\s+my\s+(\w+)", query, re.IGNORECASE)
            if match:
                service_type = match.group(1).strip()
                return self._handle_contact_lookup(service_type)
        
        # Last contact query
        if "when" in query_lower and "last" in query_lower and "contact" in query_lower:
            match = re.search(r"when (?:was|did) (?:i last|last)\s+(?:contact|call|email)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", query, re.IGNORECASE)
            if match:
                client_name = match.group(1).strip()
                return self._handle_last_contact_query(client_name)
        
        # Upcoming deadlines
        if "deadline" in query_lower and "upcoming" in query_lower:
            return self._handle_upcoming_deadlines_query()
        
        # Follow-up suggestions
        if "follow" in query_lower and "up" in query_lower:
            return self._handle_follow_up_query()
        
        return {"message": "I can help with client info, deadlines, revenue, contacts, and follow-ups. Try asking specific questions!"}

    def _handle_client_lookup(self, client_name: str) -> dict:
        """Handle client information lookup."""
        client = self._get_client_info(client_name)
        
        if not client:
            return {"message": f"I don't have any information about {client_name} yet."}
        
        lines = [f"👤 **{client['name']}**", ""]
        
        if client.get('contact_info'):
            lines.append("**Contact Info:**")
            for info_type, info_value in client['contact_info'].items():
                lines.append(f"• {info_type.title()}: {info_value}")
            lines.append("")
        
        if client.get('preferences'):
            lines.append("**Preferences:**")
            for pref in client['preferences']:
                lines.append(f"• {pref}")
            lines.append("")
        
        if client.get('last_contact'):
            lines.append(f"**Last Contact:** {client['last_contact']}")
            lines.append("")
        
        if client.get('history'):
            lines.append("**History:**")
            recent_history = sorted(client['history'], key=lambda x: x.get('date', ''), reverse=True)[:5]
            for item in recent_history:
                lines.append(f"• {item['date']}: {item.get('note', item.get('activity', 'Contact'))}")
        
        return {"message": "\n".join(lines)}

    def _handle_contact_lookup(self, service_type: str) -> dict:
        """Handle vendor/contact lookup."""
        contacts = self._load_contacts()
        
        # Find matching contact by type
        matches = [
            contact for contact in contacts.values()
            if service_type.lower() in contact.get('type', '').lower() or
               service_type.lower() in contact.get('name', '').lower()
        ]
        
        if not matches:
            return {"message": f"I don't have a {service_type} contact saved yet."}
        
        if len(matches) == 1:
            contact = matches[0]
            return {"message": f"📞 **{contact['type'].title()}:** {contact['name']} - {contact['phone']}"}
        else:
            lines = [f"📞 **{service_type.title()} Contacts:**"]
            for contact in matches:
                lines.append(f"• {contact['name']} - {contact['phone']}")
            return {"message": "\n".join(lines)}

    def _handle_last_contact_query(self, client_name: str) -> dict:
        """Handle last contact date query."""
        client = self._get_client_info(client_name)
        
        if not client:
            return {"message": f"I don't have any contact history for {client_name}."}
        
        last_contact = client.get('last_contact')
        if not last_contact:
            return {"message": f"No contact date recorded for {client_name}."}
        
        try:
            contact_date = datetime.fromisoformat(last_contact).date()
            days_ago = (date.today() - contact_date).days
            
            if days_ago == 0:
                time_desc = "today"
            elif days_ago == 1:
                time_desc = "yesterday"
            else:
                time_desc = f"{days_ago} days ago"
            
            return {"message": f"📞 Last contacted {client_name} on {last_contact} ({time_desc})"}
        except ValueError:
            return {"message": f"Last contact with {client_name}: {last_contact}"}

    def _handle_upcoming_deadlines_query(self) -> dict:
        """Handle upcoming deadlines query."""
        upcoming = self._get_upcoming_deadlines(30)  # Next 30 days
        
        if not upcoming:
            return {"message": "No upcoming deadlines in the next 30 days."}
        
        lines = ["📅 **Upcoming Deadlines:**", ""]
        
        for deadline in upcoming[:10]:  # Limit to 10
            days_until = deadline['days_until']
            if days_until < 0:
                urgency = "🚨 OVERDUE"
                time_desc = f"{abs(days_until)} days overdue"
            elif days_until == 0:
                urgency = "🔥 TODAY"
                time_desc = "due today"
            elif days_until == 1:
                urgency = "⚠️ TOMORROW"
                time_desc = "due tomorrow"
            else:
                urgency = "📍"
                time_desc = f"in {days_until} days"
            
            lines.append(f"{urgency} {deadline['task']} ({time_desc})")
        
        return {"message": "\n".join(lines)}

    def _handle_follow_up_query(self) -> dict:
        """Handle follow-up suggestions query."""
        follow_ups = self._get_follow_up_suggestions()
        
        if not follow_ups:
            return {"message": "All clients have been contacted recently!"}
        
        lines = ["💼 **Follow-up Suggestions:**", ""]
        
        for client_name, days_ago in follow_ups[:8]:  # Limit to 8
            if days_ago >= 30:
                urgency = "🚨"
            elif days_ago >= 21:
                urgency = "⚠️"
            else:
                urgency = "📞"
            
            lines.append(f"{urgency} {client_name} (last contact {days_ago} days ago)")
        
        return {"message": "\n".join(lines)}