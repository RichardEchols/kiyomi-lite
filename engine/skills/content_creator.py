"""
Kiyomi Lite — Content Creator Skill
Tracks: content ideas, posting schedule, analytics/performance, drafts
"""
import re
from datetime import datetime, timedelta

try:
    from skills.base import Skill
except ImportError:
    from engine.skills.base import Skill


# ── Keyword sets for detection ──────────────────────────────

CONTENT_KEYWORDS = [
    "post", "video", "content", "upload", "publish", "script",
    "thumbnail", "views", "subscribers", "engagement", "trending",
    "schedule", "draft", "reel", "short", "shorts", "tiktok",
    "youtube", "instagram", "twitter", "blog", "podcast",
    "caption", "hashtag", "analytics", "impressions", "clicks",
    "ctr", "watch time", "audience", "followers", "algorithm",
    "collab", "collaboration", "sponsor", "brand deal",
    "content calendar", "batch", "editing", "b-roll",
]

# ── Regex patterns for extraction ───────────────────────────

# Content idea: "video idea about...", "I should post about...", "content idea:"
IDEA_PATTERN = re.compile(
    r"(?:video\s+idea|content\s+idea|post\s+idea|idea\s+for\s+(?:a\s+)?(?:video|post|reel|short))"
    r"[:\s]+(.+?)(?:\.|$)",
    re.IGNORECASE,
)

# Broader idea capture: "I should make a video about...", "I want to post about..."
SHOULD_POST_PATTERN = re.compile(
    r"(?:should|want to|need to|going to|gonna|plan to)\s+"
    r"(?:make|create|film|shoot|record|write|post|upload)\s+"
    r"(?:a\s+)?(?:video|post|reel|short|blog|podcast|tiktok)?\s*"
    r"(?:about|on|for)?\s*(.+?)(?:\.|,|$)",
    re.IGNORECASE,
)

# Schedule: "posting on Tuesday", "scheduled for Friday", "upload date is March 5"
SCHEDULE_PATTERN = re.compile(
    r"(?:posting|scheduled?|upload(?:ing)?|publish(?:ing)?|goes?\s+live|drop(?:ping)?)"
    r"\s+(?:on|for|at|is)?\s*(.+?)(?:\.|,|$)",
    re.IGNORECASE,
)

# Analytics: "got 10K views", "hit 500 subscribers", "engagement is 5%"
VIEWS_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*[kKmM]?\s*(?:views|impressions|plays)",
    re.IGNORECASE,
)
SUBS_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*[kKmM]?\s*(?:subscribers?|followers?|subs)",
    re.IGNORECASE,
)
ENGAGEMENT_PATTERN = re.compile(
    r"(?:engagement|ctr|click.through)\s+(?:is|at|of|rate)?\s*(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
WATCH_TIME_PATTERN = re.compile(
    r"(?:watch\s+time|avg\.?\s+view\s+duration)\s+(?:is|of|at)?\s*"
    r"(\d+(?:\.\d+)?)\s*(?:min(?:ute)?s?|hours?|h|m)",
    re.IGNORECASE,
)

# Platform mention
PLATFORM_PATTERN = re.compile(
    r"\b(youtube|tiktok|instagram|twitter|x\.com|linkedin|blog|podcast|twitch|shorts?|reels?)\b",
    re.IGNORECASE,
)

# Draft: "draft of...", "working on a script for...", "outline for..."
DRAFT_PATTERN = re.compile(
    r"(?:draft|script|outline|storyboard|rough\s+cut)\s+"
    r"(?:of|for|about)?\s*(.+?)(?:\.|,|$)",
    re.IGNORECASE,
)


class ContentCreatorSkill(Skill):
    name = "content_creator"
    description = "Tracks content ideas, posting schedule, analytics, and drafts"

    def detect(self, message: str) -> bool:
        """Check if message contains content-creation-related keywords."""
        lower = message.lower()
        return any(kw in lower for kw in CONTENT_KEYWORDS)

    def extract(self, message: str, response: str = "") -> dict | None:
        """Pull structured content data from the conversation.
        Returns dict with 'entries' list of {category, entry} dicts, or None.
        """
        lower = message.lower()
        entries = []

        # Detect platform context
        platform_match = PLATFORM_PATTERN.search(message)
        platform = platform_match.group(1).lower() if platform_match else None
        # Normalize platform names
        if platform in ("shorts", "short"):
            platform = "youtube_shorts"
        elif platform in ("reels", "reel"):
            platform = "instagram_reels"
        elif platform == "x.com":
            platform = "twitter"

        # ── Content ideas ──
        idea_match = IDEA_PATTERN.search(message)
        if idea_match:
            entries.append({
                "category": "ideas",
                "entry": {
                    "idea": idea_match.group(1).strip()[:200],
                    "platform": platform,
                    "date": self.now(),
                    "status": "new",
                },
            })
        else:
            # Broader "I should post about..." capture
            should_match = SHOULD_POST_PATTERN.search(message)
            if should_match:
                idea_text = should_match.group(1).strip()
                if len(idea_text) > 5:  # Filter noise
                    entries.append({
                        "category": "ideas",
                        "entry": {
                            "idea": idea_text[:200],
                            "platform": platform,
                            "date": self.now(),
                            "status": "new",
                        },
                    })

        # ── Posting schedule ──
        schedule_match = SCHEDULE_PATTERN.search(message)
        if schedule_match:
            entries.append({
                "category": "schedule",
                "entry": {
                    "when": schedule_match.group(1).strip()[:80],
                    "platform": platform,
                    "text": message[:200],
                    "logged": self.now(),
                    "published": False,
                },
            })

        # ── Analytics / performance ──
        analytics_entry = {}
        views_match = VIEWS_PATTERN.search(message)
        if views_match:
            analytics_entry["views"] = self._parse_metric(views_match.group(0))
        subs_match = SUBS_PATTERN.search(message)
        if subs_match:
            analytics_entry["subscribers"] = self._parse_metric(subs_match.group(0))
        eng_match = ENGAGEMENT_PATTERN.search(message)
        if eng_match:
            analytics_entry["engagement_pct"] = float(eng_match.group(1))
        wt_match = WATCH_TIME_PATTERN.search(message)
        if wt_match:
            analytics_entry["watch_time"] = wt_match.group(0).strip()

        if analytics_entry:
            analytics_entry["platform"] = platform
            analytics_entry["text"] = message[:200]
            analytics_entry["date"] = self.now()
            entries.append({
                "category": "analytics",
                "entry": analytics_entry,
            })

        # ── Drafts ──
        draft_match = DRAFT_PATTERN.search(message)
        if draft_match:
            entries.append({
                "category": "drafts",
                "entry": {
                    "title": draft_match.group(1).strip()[:150],
                    "platform": platform,
                    "text": message[:300],
                    "date": self.now(),
                    "status": "in_progress",
                },
            })

        if not entries:
            return None

        return {
            "skill": self.name,
            "entries": entries,
        }

    def get_prompt_context(self) -> str:
        """Build context string for the AI system prompt."""
        lines = ["🎬 Content Creator Tracker:"]
        data = self.load_data()

        if not data:
            return ""

        has_content = False

        # ── Content pipeline (ideas not yet published) ──
        ideas = data.get("ideas", [])
        active_ideas = [i for i in ideas if i.get("status") != "published"]
        if active_ideas:
            has_content = True
            lines.append(f"  💡 Content Ideas ({len(active_ideas)} in pipeline):")
            for idea in active_ideas[-5:]:
                plat = f" [{idea['platform']}]" if idea.get("platform") else ""
                status = idea.get("status", "new")
                lines.append(f"    - {idea.get('idea', '?')}{plat} ({status}) — {idea.get('date', '')}")

        # ── Upcoming scheduled posts ──
        schedule = data.get("schedule", [])
        upcoming = [s for s in schedule if not s.get("published")]
        if upcoming:
            has_content = True
            lines.append("  📅 Upcoming Posts:")
            for s in upcoming[-5:]:
                plat = f" [{s['platform']}]" if s.get("platform") else ""
                lines.append(f"    - {s.get('when', '?')}{plat}: {s.get('text', '')[:60]}")

        # ── Recent performance ──
        analytics = data.get("analytics", [])[-3:]
        if analytics:
            has_content = True
            lines.append("  📊 Recent Performance:")
            for a in analytics:
                parts = []
                if a.get("views"):
                    parts.append(f"{a['views']} views")
                if a.get("subscribers"):
                    parts.append(f"{a['subscribers']} subs")
                if a.get("engagement_pct"):
                    parts.append(f"{a['engagement_pct']}% engagement")
                if a.get("watch_time"):
                    parts.append(f"watch: {a['watch_time']}")
                plat = f" [{a['platform']}]" if a.get("platform") else ""
                lines.append(f"    - {', '.join(parts)}{plat} — {a.get('date', '')}")

        # ── Active drafts ──
        drafts = data.get("drafts", [])
        active_drafts = [d for d in drafts if d.get("status") != "published"]
        if active_drafts:
            has_content = True
            lines.append("  ✏️ Active Drafts:")
            for d in active_drafts[-3:]:
                plat = f" [{d['platform']}]" if d.get("platform") else ""
                lines.append(f"    - {d.get('title', '?')}{plat} ({d.get('status', '?')}) — {d.get('date', '')}")

        if not has_content:
            return ""

        return "\n".join(lines)

    def get_proactive_nudges(self) -> list[str]:
        """Return actionable nudges for the content creator."""
        nudges = []
        data = self.load_data()

        if not data:
            return nudges

        # 1. Posting schedule reminders (unpublished scheduled posts)
        schedule = data.get("schedule", [])
        upcoming = [s for s in schedule if not s.get("published")]
        for s in upcoming[-3:]:
            plat = f" on {s['platform']}" if s.get("platform") else ""
            nudges.append(
                f"📅 You have a post scheduled{plat} for {s.get('when', '?')}. "
                f"Is it ready to go?"
            )

        # 2. Content gaps — haven't logged ideas recently
        ideas = data.get("ideas", [])
        if not ideas:
            nudges.append(
                "💡 Your content idea bank is empty! "
                "Try brainstorming a few video or post ideas."
            )
        elif len(ideas) <= 2:
            nudges.append(
                f"💡 You only have {len(ideas)} content idea(s) saved. "
                f"Consider adding more to stay ahead of your schedule."
            )

        # 3. Stale ideas — ideas sitting in 'new' status
        new_ideas = [i for i in ideas if i.get("status") == "new"]
        if len(new_ideas) >= 5:
            nudges.append(
                f"📋 You have {len(new_ideas)} content ideas that haven't been "
                f"started yet. Pick one and start creating!"
            )

        # 4. Engagement follow-ups — check recent analytics
        analytics = data.get("analytics", [])[-3:]
        for a in analytics:
            if a.get("engagement_pct") and a["engagement_pct"] < 2.0:
                nudges.append(
                    f"📉 Recent engagement is at {a['engagement_pct']}%. "
                    f"Consider reviewing your content strategy or posting times."
                )
                break
            if a.get("views") and isinstance(a["views"], str) and "0" == a["views"]:
                nudges.append(
                    "📊 A recent post got very few views. "
                    "Check your titles, thumbnails, and posting time."
                )
                break

        # 5. Drafts sitting too long
        drafts = data.get("drafts", [])
        in_progress = [d for d in drafts if d.get("status") == "in_progress"]
        if len(in_progress) >= 3:
            nudges.append(
                f"✏️ You have {len(in_progress)} drafts in progress. "
                f"Focus on finishing one before starting another!"
            )

        return nudges

    # ── Private helpers ─────────────────────────────────────

    @staticmethod
    def _parse_metric(raw: str) -> str:
        """Normalize a metric string like '10K views' → '10K'.
        Returns the string as-is for display; actual numeric parsing
        would need more logic for K/M suffixes.
        """
        # Extract just the number+suffix part
        match = re.match(r"([\d,.]+\s*[kKmM]?)", raw)
        return match.group(1).strip() if match else raw.strip()

    @staticmethod
    def _format_entry(category: str, entry: dict) -> str:
        """Format a single entry for display."""
        if category == "ideas":
            plat = f" [{entry['platform']}]" if entry.get("platform") else ""
            return f"💡 {entry.get('idea', '?')}{plat} — {entry.get('date', '')}"
        elif category == "schedule":
            plat = f" [{entry['platform']}]" if entry.get("platform") else ""
            status = "✅" if entry.get("published") else "📅"
            return f"{status} {entry.get('when', '?')}{plat} — {entry.get('text', '')[:50]}"
        elif category == "analytics":
            parts = []
            if entry.get("views"):
                parts.append(f"{entry['views']} views")
            if entry.get("subscribers"):
                parts.append(f"{entry['subscribers']} subs")
            if entry.get("engagement_pct"):
                parts.append(f"{entry['engagement_pct']}%")
            return f"📊 {', '.join(parts) or '?'} — {entry.get('date', '')}"
        elif category == "drafts":
            status = entry.get("status", "?")
            return f"✏️ {entry.get('title', '?')} ({status}) — {entry.get('date', '')}"
        return str(entry)
