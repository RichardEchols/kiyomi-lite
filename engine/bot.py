#!/usr/bin/env python3
"""
Kiyomi Lite — Telegram Bot
Simple. Clean. Just works.

User messages Kiyomi → Kiyomi responds using their AI → memory builds silently.
No terminal. No dashboard. No complexity visible.
"""
import asyncio
import logging
import sys
import time
from pathlib import Path

# Add engine dir to path
sys.path.insert(0, str(Path(__file__).parent))

from telegram import Update, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from telegram.constants import ChatAction

from config import load_config, save_config, get_api_key, CONFIG_DIR, MEMORY_DIR
from router import classify_message, pick_model
from ai import chat
from memory import log_conversation, get_recent_context, extract_and_remember, load_all_memory, extract_facts_from_message, save_fact, export_memory, lookup_person
from multi_user import UserManager
from reminders import parse_reminder_from_message, add_reminder, list_active_reminders
from skills_integration import (
    run_post_message_hook, get_skills_prompt_context,
    get_skill_capabilities_prompt
)
from url_reader import find_urls, read_urls_in_message
from get_to_know import (
    is_onboarding_active, is_onboarding_complete,
    start_onboarding, handle_onboarding_message
)
from calendar_integration import (
    get_todays_events, get_upcoming_events, create_event,
    find_free_time, morning_briefing, setup_calendar,
    is_calendar_configured
)

_bot_log_handlers = [logging.FileHandler(CONFIG_DIR / "logs" / "kiyomi.log")]
if sys.stdout is not None:
    _bot_log_handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_bot_log_handlers,
)
logger = logging.getLogger("kiyomi")

# Multi-user manager
user_manager = UserManager()

# Simple conversation history (in-memory, last 20 messages)
conversation_history: list = []


def get_bot_name(config: dict) -> str:
    """Get the bot's display name. Defaults to 'Kiyomi' if not set."""
    return config.get("bot_name", "Kiyomi")


def build_system_prompt(config: dict, user_dir: Path = None) -> str:
    """Build personality prompt using the bot's actual name."""
    name = config.get("name", "there")
    bot_name = get_bot_name(config)
    
    # Load deep memory (categorized facts, documents, recent conversations)
    memory_block = load_all_memory(user_dir=user_dir)
    
    # Get skill context (health, budget, tasks data)
    skills_context = get_skills_prompt_context()
    capabilities = get_skill_capabilities_prompt()
    
    prompt = f"""You are {bot_name} — {name}'s personal assistant. Not a chatbot. Not an app. You are their EMPLOYEE.

Think of yourself as a real assistant who works for {name}. You know their life, their family, their work, their health, their preferences. You USE that knowledge constantly. You don't wait to be asked — you anticipate needs, follow up on things, and get work done.

How you behave:
- SHORT replies. 1-3 sentences for casual chat. Like texting a coworker, not a customer service bot.
- NEVER repeat what they said. NEVER ask "anything else?" — just handle it.
- REFERENCE what you know about them naturally. If you know their spouse's name, USE it. If you know they take meds, ASK about it. This is what makes you irreplaceable.
- When they tell you to do something, DO IT IMMEDIATELY. Don't ask "would you like me to...?" — an employee doesn't ask permission to do their job.
- Remember EVERYTHING. Every detail they share is important. Names, dates, preferences, complaints, goals — all of it.

{f"WHAT I KNOW ABOUT {name.upper()} (use this naturally in conversation):" if memory_block else ""}
{memory_block}

{skills_context}
{capabilities}

FOR BUSINESS USERS: If {name} runs a business, you are their virtual employee. You remember clients, cases, deadlines, contacts. You draft documents, track tasks, remind about follow-ups. You are more reliable than a human assistant because you never forget. A lawyer tells you about a case once — you remember the client name, opposing counsel, deadlines, and key facts FOREVER.


TOOLS: You have real tools you can use silently:
- web_search: Search the internet. Use for ANY current info (weather, news, prices, scores, facts you're unsure about). ALWAYS search rather than guessing.
- read_url: Read any webpage. When {name} sends a link, use this to read it and summarize.
- run_code: Run Python code. Use for math, calculations, date math, unit conversions, data processing.
- remember: Save important facts about {name} to long-term memory. Use when you learn something worth remembering.
- read_file: Read files from {name}'s data folder.
- create_file: Create .docx or .txt documents. Use when {name} asks you to write a resume, letter, report, or any document they can download. The file will be sent to them automatically.
- send_email: Send an email via Gmail. Use when {name} asks you to email someone.
- analyze_image: Analyze an image file. Use when {name} sends a photo or asks about an image.

CRITICAL RULES:
1. DO NOT ASK FOR CONFIRMATION — when {name} asks you to do something, DO IT. Don't say "Would you like me to..." or "Shall I..." — just do the thing. Action > asking.
2. DO NOT RE-ASK for info you already have. If they told you their name, job, details — USE them. Don't ask again.
3. USE TOOLS PROACTIVELY:
   - Something current or uncertain? → web_search immediately. Never say "I don't have that info."
   - They send a URL? → read_url immediately.
   - Math/calculations needed? → run_code.
   - They ask to write/create/draft ANY document? → create_file IMMEDIATELY. Make a real .docx file. Do NOT type the document in chat — that's useless on a phone. The file auto-delivers in Telegram.
   - They say "yes" or agree to something? → DO IT NOW. Don't ask more questions you already know the answer to.
   - They send a photo? → analyze_image.
   - You learn something about them? → remember silently.
4. KEEP RESPONSES SHORT. This is Telegram on a phone. 2-4 sentences max for casual chat. Only go longer when they ask for detailed info.
5. When you create a file, tell them briefly what you made (1 sentence). The file appears automatically right after your message.
"""
    return prompt.strip()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages — the core loop."""
    if not update.message or not update.message.text:
        return
    
    config = load_config()
    user_msg = update.message.text.strip()
    
    # Get user's Telegram ID and first name
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name or "User"
    
    # Get or create user and their memory directory
    user_info = user_manager.get_or_create_user(telegram_id, first_name)
    user_memory_dir = user_manager.get_user_memory_dir(telegram_id)
    
    # Store Telegram user ID on first message
    if not config.get("telegram_user_id"):
        config["telegram_user_id"] = telegram_id
        save_config(config)
    
    # Show typing indicator
    await update.message.chat.send_action(ChatAction.TYPING)
    
    # --- Natural memory extraction (no onboarding gate) ---
    # Extract personal facts from every message silently
    facts = extract_facts_from_message(user_msg, user_dir=user_memory_dir)
    for fact, category in facts:
        save_fact(fact, category, user_dir=user_memory_dir)
    
    # --- Mood / pattern detection ---
    msg_lower = user_msg.lower()
    _MOOD_INDICATORS = {
        "stressed": ["stressed", "overwhelmed", "too much", "can't handle", "exhausted", "burned out"],
        "happy": ["great day", "excited", "amazing", "wonderful", "celebration", "promoted", "won"],
        "sad": ["sad", "depressed", "lonely", "miss", "lost", "grief", "crying"],
        "anxious": ["worried", "anxious", "nervous", "scared", "can't sleep"],
    }
    for mood, keywords in _MOOD_INDICATORS.items():
        matched_keyword = next((kw for kw in keywords if kw in msg_lower), None)
        if matched_keyword:
            brief_context = user_msg[:80].replace("\n", " ")
            save_fact(f"Mood: {mood} - {brief_context}", "other", user_dir=user_memory_dir)
            break  # Only save one mood per message
    
    # Check for reminder
    reminder_info = parse_reminder_from_message(user_msg)
    if reminder_info:
        reminder = add_reminder(
            reminder_info["text"],
            reminder_info["time"],
            reminder_info["recurring"]
        )
        freq = "every day" if reminder["recurring"] else "once"
        await update.message.reply_text(
            f"Got it! I'll remind you: \"{reminder_info['text']}\" "
            f"({freq} at {reminder_info['time']}) ✅"
        )
        log_conversation(user_msg, f"[Reminder set: {reminder_info['text']}]", user_dir=user_memory_dir)
        return
    
    # Classify and route
    task_type = classify_message(user_msg)
    provider, model = pick_model(task_type, config)
    api_key = config.get(f"{provider}_key", "")
    
    if not api_key:
        api_key = get_api_key(config)
    
    if not api_key:
        await update.message.reply_text(
            "I'm not connected to an AI service yet! 😅\n\n"
            "Open Kiyomi settings to connect your AI account."
        )
        return
    
    # Build system prompt with memory
    system_prompt = build_system_prompt(config, user_dir=user_memory_dir)
    
    # Snapshot files dir BEFORE AI call (to detect new files after)
    files_dir = CONFIG_DIR / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    files_before = set(files_dir.iterdir()) if files_dir.exists() else set()
    
    # Chat with AI
    response = await chat(
        message=user_msg,
        provider=provider,
        model=model,
        api_key=api_key,
        system_prompt=system_prompt,
        history=conversation_history[-20:],
    )
    
    # Update conversation history (store original message, not augmented)
    conversation_history.append({"role": "user", "content": user_msg})
    conversation_history.append({"role": "assistant", "content": response})
    
    # Keep history manageable
    if len(conversation_history) > 40:
        conversation_history[:] = conversation_history[-20:]
    
    # Save to memory (silently)
    log_conversation(user_msg, response, user_dir=user_memory_dir)
    extract_and_remember(user_msg, response, user_dir=user_memory_dir)
    
    # Run skills post-message hook (detect & extract health, budget, tasks)
    run_post_message_hook(user_msg, response)
    
    # Voice reply check — respond with audio if appropriate
    try:
        from voice_reply import should_use_voice, generate_voice_reply
        if should_use_voice(user_msg, config):
            audio_path = await generate_voice_reply(response, config)
            if audio_path:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
                # Still send text too (for readability)
    except ImportError:
        pass  # Voice module not available
    except Exception as e:
        logger.error(f"Voice reply error: {e}")
    
    # Send response
    # Split long messages for Telegram (4096 char limit)
    if len(response) > 4000:
        chunks = [response[i:i+4000] for i in range(0, len(response), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk)
    else:
        await update.message.reply_text(response)

    # Send any NEW files created during this AI call
    if files_dir.exists():
        files_after = set(files_dir.iterdir())
        new_files = files_after - files_before
        for f in new_files:
            if f.is_file():
                try:
                    await update.message.reply_document(
                        document=open(f, "rb"), filename=f.name
                    )
                    logger.info(f"Sent file: {f.name}")
                except Exception as e:
                    logger.error(f"Failed to send file {f.name}: {e}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages — transcribe and extract action items."""
    await update.message.reply_chat_action(ChatAction.TYPING)

    try:
        # Use the new voice notes system
        from voice_notes import handle_voice_message
        response = await handle_voice_message(update, context)
        await update.message.reply_text(response[:4000])
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("Hmm, I couldn't catch that. Mind typing it out? 🎤")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos — download and analyze with AI vision.
    
    Receipt detection: if the caption mentions 'receipt' or the user recently
    asked to scan a receipt, route to the receipt scanner instead of generic
    image analysis.
    """
    config = load_config()

    # Get user's memory directory
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name or "User"
    user_info = user_manager.get_or_create_user(telegram_id, first_name)
    user_memory_dir = user_manager.get_user_memory_dir(telegram_id)

    await update.message.reply_chat_action(ChatAction.TYPING)

    try:
        # Get highest res photo
        photo = update.message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)

        # Save to temp
        photo_path = CONFIG_DIR / "temp" / f"photo_{photo.file_id}.jpg"
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        await photo_file.download_to_drive(photo_path)

        caption = update.message.caption or ""

        # --- Receipt detection (check BEFORE generic analysis) ---
        try:
            from receipt_scanner import looks_like_receipt_request, scan_receipt, process_receipt

            # Gather recent user messages for context
            recent_user_msgs = [
                m["content"] for m in conversation_history[-6:]
                if m.get("role") == "user"
            ]

            if looks_like_receipt_request(caption, recent_user_msgs):
                # Route to receipt scanner
                logger.info("Receipt detected — routing to receipt scanner")
                scan_result = scan_receipt(str(photo_path), config)
                response = process_receipt(scan_result)

                await update.message.reply_text(response[:4000], parse_mode="Markdown")

                # Log to conversation history
                log_conversation(
                    f"[Photo: receipt scan] {caption}".strip(),
                    response,
                    user_dir=user_memory_dir
                )

                # Cleanup
                photo_path.unlink(missing_ok=True)
                return
        except ImportError:
            logger.warning("receipt_scanner module not available")
        except Exception as e:
            logger.error(f"Receipt scan error: {e}")
            # Fall through to generic analysis

        # --- Generic image analysis ---
        if not caption:
            caption = "What's in this image? Describe it and help me with whatever I might need."

        # Use analyze_image tool
        from tools import analyze_image

        result = analyze_image(str(photo_path), caption)

        await update.message.reply_text(result[:4000])

        # Cleanup
        photo_path.unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.message.reply_text("Sorry, I couldn't process that image. Try sending it again? 📷")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads — read and process."""
    config = load_config()

    # Get user's memory directory
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name or "User"
    user_info = user_manager.get_or_create_user(telegram_id, first_name)
    user_memory_dir = user_manager.get_user_memory_dir(telegram_id)

    await update.message.reply_chat_action(ChatAction.TYPING)

    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or "unknown"
    mime = doc.mime_type or ""

    try:
        file = await context.bot.get_file(doc.file_id)
        file_path = CONFIG_DIR / "temp" / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        await file.download_to_drive(file_path)

        # Determine type and extract text
        suffix = file_path.suffix.lower()
        content = ""

        if suffix == ".pdf":
            try:
                import subprocess

                result = subprocess.run(
                    ["pdftotext", str(file_path), "-"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    content = result.stdout[:5000]
                else:
                    content = "[Could not extract PDF text — pdftotext not available]"
            except Exception:
                content = "[Could not extract PDF text]"

        elif suffix == ".docx":
            try:
                from docx import Document as DocxDocument

                doc_obj = DocxDocument(str(file_path))
                content = "\n".join(p.text for p in doc_obj.paragraphs)[:5000]
            except Exception:
                content = "[Could not read .docx file]"

        elif suffix in (".txt", ".md", ".csv", ".json"):
            content = file_path.read_text(encoding="utf-8", errors="replace")[:5000]

        elif suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            from tools import analyze_image

            caption = update.message.caption or "What's in this image?"
            result = analyze_image(str(file_path), caption)
            await update.message.reply_text(result[:4000])
            file_path.unlink(missing_ok=True)
            return

        else:
            await update.message.reply_text(
                f"I received {filename} but I'm not sure how to read that file type yet. "
                f"I can handle .pdf, .docx, .txt, .md, .csv, and images! 📁"
            )
            file_path.unlink(missing_ok=True)
            return

        if content:
            # SAVE document to memory so AI can reference it later
            docs_dir = user_memory_dir / "documents"
            docs_dir.mkdir(parents=True, exist_ok=True)
            safe_name = filename.rsplit(".", 1)[0][:50]  # strip extension, limit length
            doc_md = docs_dir / f"{safe_name}.md"
            doc_md.write_text(
                f"# {filename}\n"
                f"*Received: {time.strftime('%Y-%m-%d %H:%M')}*\n\n"
                f"{content}\n"
            )
            logger.info(f"Saved document to memory: {doc_md}")

            # Pass to AI for analysis
            from router import classify_message, pick_model

            system_prompt = build_system_prompt(config, user_dir=user_memory_dir)
            task_type = classify_message(f"analyze file: {filename}")
            provider, model = pick_model(task_type, config)
            api_key = config.get(f"{provider}_key", "") or get_api_key(config)
            prompt = (
                f"The user sent a file called '{filename}'. Here's the content:\n\n"
                f"{content}\n\n"
                f"I've saved this to memory so I can reference it anytime. "
                f"Summarize this file and ask how you can help with it."
            )
            response = await chat(
                message=prompt,
                provider=provider,
                model=model,
                api_key=api_key,
                system_prompt=system_prompt,
            )
            await update.message.reply_text(response[:4000])

        file_path.unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"Document error: {e}")
        await update.message.reply_text(
            f"Sorry, I had trouble reading {filename}. Try a different format? 📁"
        )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command — first contact."""
    config = load_config()
    name = config.get("name", "")
    bot_name = get_bot_name(config)
    
    if name:
        await update.message.reply_text(
            f"Hey {name}! 🌸 I'm here and ready to help.\n\n"
            f"Just message me anything — I'm your personal assistant!"
        )
    else:
        await update.message.reply_text(
            f"Hi there! 🌸 I'm {bot_name}, your personal AI assistant.\n\n"
            "What's your name? I'd love to get to know you!"
        )
        context.user_data["awaiting_name"] = True


async def cmd_gettoknow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restart the Get to Know You flow."""
    config = load_config()
    name = config.get("name", "there")
    intro = start_onboarding(name)
    await update.message.reply_text(intro)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command — show what Kiyomi can do."""
    config = load_config()
    name = config.get("name", "there")
    await update.message.reply_text(
        f"I'm your personal assistant, {name}. Here's what I do:\n\n"
        "🧠 **I Remember Everything** — Tell me once, I know it forever\n"
        "📄 **I Create Documents** — Resumes, letters, reports → delivered as files\n"
        "⏰ **I Remind You** — Meds, meetings, deadlines — I never forget\n"
        "🌅 **I Check In** — Morning briefs with weather, reminders, health\n"
        "💊 **I Track Health** — Meds, vitals, symptoms, appointments\n"
        "💰 **I Track Money** — Spending and income, naturally\n"
        "📋 **I Track Tasks** — To-dos caught from conversation\n"
        "🗓️ **I Manage Your Calendar** — Google Calendar integration\n"
        "🔗 **I Read Links** — Send me any URL\n"
        "🔍 **I Search** — Current info, weather, news, anything\n\n"
        "**Just talk to me like a real person.** I pick up on:\n"
        "• \"My wife Sarah has a birthday March 15\"\n"
        "• \"Remind me to file the Johnson brief by Friday\"\n"
        "• \"Draft a demand letter for the Smith case\"\n"
        "• \"I took my blood pressure, it was 130/80\"\n\n"
        "🧾 **I Scan Receipts** — Send a photo with the caption \"receipt\":\n"
        "• I'll read every item, total, tax, and payment method\n"
        "• Auto-categorize and add to your budget tracker\n"
        "• Keep a searchable history of all your receipts\n\n"
        "🏦 **I Track Real Finances** — Connect your bank and ask:\n"
        "• \"How much did I spend on food this week?\"\n"
        "• \"What's my bank balance?\"\n"
        "• \"Am I on budget this month?\"\n\n"
        "**Commands:**\n"
        "/memory — See everything I remember about you\n"
        "/health — Your health summary\n"
        "/budget — Your spending summary\n"
        "/tasks — Your task list\n"
        "/reminders — Your active reminders\n"
        "/receipts — Recent scanned receipts\n"
        "/connect — Connect your bank account\n"
        "/calendar — Today's events and calendar commands\n"
        "/profile — Your personal profile card 🪪\n"
        "/profile full — Full profile as a document\n"
        "/profile doctor — Health card for your doctor\n"
        "/export — Export your profile\n"
        "/lookup [name] — Search memory for a person\n"
        "/help — Show this message\n"
        "/forget — Clear my memory\n\n"
        "The more we talk, the more useful I become. 💛",
        parse_mode="Markdown"
    )


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reminders command."""
    reminders = list_active_reminders()
    if not reminders:
        await update.message.reply_text("No active reminders! Tell me to remind you about something 😊")
        return
    
    text = "📋 Your reminders:\n\n"
    for r in reminders:
        freq = "🔁" if r.get("recurring") else "⏰"
        text += f"{freq} {r['text']} — {r['time']}\n"
    
    await update.message.reply_text(text)


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show health tracking summary."""
    try:
        from skills.health import HealthSkill
        skill = HealthSkill()
        ctx = skill.get_prompt_context()
        if ctx and ctx.strip():
            await update.message.reply_text(f"💊 Your Health Summary\n\n{ctx}")
        else:
            await update.message.reply_text(
                "No health data tracked yet! 💊\n\n"
                "Just mention things naturally:\n"
                "• \"I took my blood pressure, it was 130/80\"\n"
                "• \"Took my meds this morning\"\n"
                "• \"I walked 5000 steps today\""
            )
    except ImportError:
        await update.message.reply_text("Health tracking is being set up! 🔧")


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show budget tracking summary."""
    try:
        from skills.budget import BudgetSkill
        skill = BudgetSkill()
        ctx = skill.get_prompt_context()
        if ctx and ctx.strip():
            await update.message.reply_text(f"💰 Your Budget Summary\n\n{ctx}")
        else:
            await update.message.reply_text(
                "No spending tracked yet! 💰\n\n"
                "Just mention expenses naturally:\n"
                "• \"Spent $45 at Kroger\"\n"
                "• \"Paid $120 for electric bill\"\n"
                "• \"Got paid $3000 today\""
            )
    except ImportError:
        await update.message.reply_text("Budget tracking is being set up! 🔧")


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show task list."""
    try:
        from skills.tasks import TaskSkill
        skill = TaskSkill()
        ctx = skill.get_prompt_context()
        if ctx and ctx.strip():
            await update.message.reply_text(f"📋 Your Tasks\n\n{ctx}")
        else:
            await update.message.reply_text(
                "No tasks tracked yet! 📋\n\n"
                "Just mention things naturally:\n"
                "• \"I need to call the doctor tomorrow\"\n"
                "• \"Don't let me forget to pay rent\"\n"
                "• \"I have to finish the report by Friday\""
            )
    except ImportError:
        await update.message.reply_text("Task tracking is being set up! 🔧")


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show what Kiyomi remembers — organized by category."""
    from memory import CATEGORIES, MEMORY_DIR, get_memory_summary
    
    # Get user's memory directory
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name or "User"
    user_info = user_manager.get_or_create_user(telegram_id, first_name)
    user_memory_dir = user_manager.get_user_memory_dir(telegram_id)
    
    summary = get_memory_summary(user_dir=user_memory_dir)
    config = load_config()
    bot_name = get_bot_name(config)
    
    lines = [f"🧠 **What {bot_name} Remembers**\n"]
    
    total_facts = 0
    for cat_key, (filename, display_name) in CATEGORIES.items():
        info = summary.get(cat_key, {})
        count = info.get("facts", 0)
        total_facts += count
        if count > 0:
            # Read actual facts for display
            filepath = user_memory_dir / filename
            if filepath.exists():
                content = filepath.read_text(encoding="utf-8", errors="replace")
                fact_lines = [l.strip() for l in content.splitlines() if l.strip().startswith("- ")]
                # Show up to 5 facts per category
                display = fact_lines[-5:]
                emoji_map = {
                    "identity": "👤", "family": "👨‍👩‍👧‍👦", "work": "💼",
                    "health": "💊", "preferences": "⭐", "goals": "🎯",
                    "schedule": "📅", "other": "📝"
                }
                emoji = emoji_map.get(cat_key, "📌")
                lines.append(f"\n{emoji} **{display_name}** ({count} facts)")
                for fl in display:
                    # Clean up timestamp for display
                    clean = fl.replace("- ", "  • ", 1)
                    lines.append(clean[:100])
    
    if total_facts == 0:
        lines.append(f"\nI don't know much about you yet! Just talk to me naturally and I'll start remembering. 💛")
    else:
        lines.append(f"\n📊 **Total: {total_facts} facts remembered**")
        lines.append(f"\nThe more we talk, the more I learn about you. Everything here helps me be a better assistant.")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /export command — send memory as .md and .docx files."""
    import tempfile
    from tools import markdown_to_docx

    config = load_config()
    name = config.get("name", "there")

    # Get user's memory directory
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name or "User"
    user_info = user_manager.get_or_create_user(telegram_id, first_name)
    user_memory_dir = user_manager.get_user_memory_dir(telegram_id)

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        md_content = export_memory(user_dir=user_memory_dir)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write markdown file
            md_path = Path(tmpdir) / f"kiyomi_memory_{name}.md"
            md_path.write_text(md_content, encoding="utf-8")

            # Convert to docx
            docx_path = Path(tmpdir) / f"kiyomi_memory_{name}.docx"
            doc = markdown_to_docx(md_content, title=f"Everything I Know About {name}")
            doc.save(str(docx_path))

            await update.message.reply_text(f"Here's everything I know about you, {name}! 📋")

            # Send both files
            with open(md_path, "rb") as f:
                await update.message.reply_document(document=f, filename=md_path.name)
            with open(docx_path, "rb") as f:
                await update.message.reply_document(document=f, filename=docx_path.name)

    except Exception as e:
        logger.error(f"Export error: {e}")
        await update.message.reply_text("Sorry, I had trouble exporting your memory. Try again? 😅")


async def cmd_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /lookup command — search memory for a person."""
    config = load_config()
    name_query = " ".join(context.args) if context.args else ""

    if not name_query:
        await update.message.reply_text(
            "Who should I look up? Use: /lookup Mrs. Davis"
        )
        return

    # Get user's memory directory
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name or "User"
    user_info = user_manager.get_or_create_user(telegram_id, first_name)
    user_memory_dir = user_manager.get_user_memory_dir(telegram_id)

    await update.message.chat.send_action(ChatAction.TYPING)

    result = lookup_person(name_query, user_dir=user_memory_dir)

    # Split if too long for Telegram
    if len(result) > 4000:
        chunks = [result[i:i+4000] for i in range(0, len(result), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode="Markdown")
    else:
        await update.message.reply_text(result, parse_mode="Markdown")


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /forget command — clear memory."""
    await update.message.reply_text(
        "Are you sure you want me to forget everything? "
        "Send /confirmforget to proceed."
    )


async def cmd_confirmforget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actually clear memory."""
    import shutil
    
    # Get user's memory directory
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name or "User"
    user_info = user_manager.get_or_create_user(telegram_id, first_name)
    user_memory_dir = user_manager.get_user_memory_dir(telegram_id)
    
    if user_memory_dir and user_memory_dir.exists():
        shutil.rmtree(user_memory_dir)
        user_memory_dir.mkdir(parents=True, exist_ok=True)
    conversation_history.clear()
    await update.message.reply_text("Memory cleared. Fresh start! 🌱")


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /profile — generate the Know Me profile card."""
    config = load_config()
    await update.message.reply_chat_action(ChatAction.TYPING)
    
    try:
        from profile_card import generate_compact_card, generate_profile_card, generate_doctor_card
        
        args = context.args
        if args and args[0].lower() == "doctor":
            card = generate_doctor_card(config)
            await update.message.reply_text(card[:4000], parse_mode="Markdown")
            return
        
        if args and args[0].lower() == "full":
            card = generate_profile_card(config)
            # Full profile might be long — save as file
            profile_path = Path.home() / ".kiyomi" / "files" / "my_profile.md"
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(card)
            await update.message.reply_document(
                document=open(profile_path, "rb"),
                filename="My_Kiyomi_Profile.md",
                caption="Here's your complete profile! 🪪"
            )
            return
        
        # Default: compact card
        card = generate_compact_card(config)
        await update.message.reply_text(card[:4000], parse_mode="Markdown")
    
    except ImportError:
        await update.message.reply_text("Profile card feature not available in this version.")
    except Exception as e:
        logger.error(f"Profile card error: {e}")
        await update.message.reply_text("Hmm, had trouble generating your profile. Try again?")


async def cmd_receipts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent receipt scan history."""
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        from receipt_scanner import get_receipt_history
        days = 30
        if context.args:
            try:
                days = int(context.args[0])
            except ValueError:
                pass
        result = get_receipt_history(days)
        await update.message.reply_text(result[:4000], parse_mode="Markdown")
    except ImportError:
        await update.message.reply_text("Receipt scanning is being set up! 🔧")
    except Exception as e:
        logger.error(f"Receipts command error: {e}")
        await update.message.reply_text("Sorry, I had trouble loading receipt history. 😅")


async def cmd_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /connect — connect a bank account via Plaid."""
    try:
        from plaid_integration import is_bank_connected, get_connected_banks
        
        config = load_config()
        plaid_cfg = config.get("plaid", {})
        client_id = plaid_cfg.get("client_id", "")
        secret = plaid_cfg.get("secret", "")
        
        if not client_id or not secret:
            await update.message.reply_text(
                "🏦 **Bank Connection**\n\n"
                "Plaid isn't set up yet. To connect your bank:\n\n"
                "1. Open Kiyomi Settings\n"
                "2. Go to Integrations → Plaid\n"
                "3. Add your Plaid API keys\n"
                "4. Then run /connect again\n\n"
                "Get free API keys at https://dashboard.plaid.com",
                parse_mode="Markdown",
            )
            return
        
        if is_bank_connected():
            banks = get_connected_banks()
            bank_list = "\n".join(
                f"  ✅ {b['institution']} (connected {b['connected_at'][:10]})"
                for b in banks
            )
            await update.message.reply_text(
                f"🏦 **Connected Banks**\n\n{bank_list}\n\n"
                "Try:\n"
                "• \"How much did I spend this week?\"\n"
                "• \"What's my bank balance?\"\n"
                "• \"How much did I spend on food?\"\n\n"
                "To add another bank, use the Kiyomi app.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "🏦 **Connect Your Bank**\n\n"
                "To link your bank account, open the Kiyomi app "
                "and tap 'Connect Bank' in Settings.\n\n"
                "Once connected, you can ask me:\n"
                "• \"How much did I spend this month?\"\n"
                "• \"What's my balance?\"\n"
                "• \"Am I on budget?\"\n\n"
                "Your data stays private — only you and I can see it. 🔒",
                parse_mode="Markdown",
            )
    except ImportError:
        await update.message.reply_text(
            "Bank connection isn't available in this version. "
            "Update Kiyomi to get Plaid integration!"
        )


async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /calendar command — show today's events or set up calendar."""
    if not is_calendar_configured():
        result = setup_calendar()
        await update.message.reply_text(result, parse_mode="Markdown")
        return
    
    args = context.args
    
    await update.message.chat.send_action(ChatAction.TYPING)
    
    try:
        if not args:
            # Default: show today's events
            result = get_todays_events()
        elif args[0].lower() in ["today"]:
            result = get_todays_events()
        elif args[0].lower() in ["week", "upcoming"]:
            days = 7
            if len(args) > 1 and args[1].isdigit():
                days = int(args[1])
            result = get_upcoming_events(days)
        elif args[0].lower() in ["free", "available"]:
            date_arg = args[1] if len(args) > 1 else ""
            result = find_free_time(date_arg)
        elif args[0].lower() in ["setup", "config"]:
            result = setup_calendar()
        else:
            result = (
                "🗓️ **Calendar Commands:**\n\n"
                "/calendar — Today's events\n"
                "/calendar today — Today's events\n"
                "/calendar week — Next 7 days\n"
                "/calendar upcoming 14 — Next 14 days\n"
                "/calendar free — Free time today\n"
                "/calendar free 2025-02-15 — Free time on specific date\n"
                "/calendar setup — Setup Google Calendar"
            )
        
        # Split long responses
        if len(result) > 4000:
            chunks = [result[i:i+4000] for i in range(0, len(result), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode="Markdown")
        else:
            await update.message.reply_text(result, parse_mode="Markdown")
            
    except Exception as e:
        logger.error(f"Calendar command error: {e}")
        await update.message.reply_text(f"❌ Calendar error: {str(e)[:200]}")




async def proactive_check_loop(app: Application):
    """Run proactive checks every 4 hours."""
    try:
        from skills.proactive import run_proactive_check
        config = load_config()
        chat_id = config.get("telegram_user_id", "")
        if not chat_id:
            logger.info("No user ID yet — skipping proactive checks until first message")
            return
        
        while True:
            await asyncio.sleep(4 * 60 * 60)  # 4 hours
            try:
                await run_proactive_check(app.bot, chat_id)
            except Exception as e:
                logger.error(f"Proactive check failed: {e}")
    except ImportError:
        logger.info("Proactive module not available — running without proactive checks")
    except Exception as e:
        logger.error(f"Proactive loop error: {e}")


async def post_init(app: Application):
    """Called after bot starts — detect bot name and kick off background tasks."""
    # Detect bot's display name from Telegram
    try:
        bot_info = await app.bot.get_me()
        bot_display_name = bot_info.first_name or "Kiyomi"
        config = load_config()
        if config.get("bot_name") != bot_display_name:
            config["bot_name"] = bot_display_name
            config["bot_username"] = bot_info.username or ""
            save_config(config)
            logger.info(f"🌸 Bot identity: {bot_display_name} (@{bot_info.username})")
    except Exception as e:
        logger.warning(f"Could not detect bot name: {e}")
    
    asyncio.create_task(proactive_check_loop(app))
    
    # Start the Scheduler (fires reminders, morning briefs, skill nudges)
    try:
        from scheduler import Scheduler
        config = load_config()
        chat_id = config.get("telegram_user_id", "")
        if chat_id:
            sched = Scheduler(app.bot, chat_id)
            asyncio.create_task(sched.run())
            logger.info("🌸 Scheduler started (reminders, morning brief, nudges)")
        else:
            logger.info("No user ID yet — scheduler will start on first message")
    except Exception as e:
        logger.warning(f"Scheduler failed to start: {e}")
    
    logger.info("🌸 Background tasks started")


def _build_app():
    """Build the Telegram application with all handlers."""
    config = load_config()
    token = config.get("telegram_token", "")
    
    if not token:
        logger.error("No Telegram bot token configured! Run Kiyomi setup first.")
        return None
    
    app = Application.builder().token(token).post_init(post_init).build()
    
    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("confirmforget", cmd_confirmforget))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("gettoknow", cmd_gettoknow))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("lookup", cmd_lookup))
    app.add_handler(CommandHandler("connect", cmd_connect))
    app.add_handler(CommandHandler("receipts", cmd_receipts))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("calendar", cmd_calendar))
    
    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    return app


def main():
    """Start the bot (works from main thread only — uses signal handlers)."""
    app = _build_app()
    if not app:
        sys.exit(1)
    logger.info("🌸 Kiyomi is starting up...")
    app.run_polling(drop_pending_updates=True)


def main_threaded():
    """Start the bot from a background thread (no signal handlers).
    
    Used when running inside the PyInstaller menu bar app.
    Uses the lower-level API to avoid set_wakeup_fd errors.
    """
    app = _build_app()
    if not app:
        raise RuntimeError("No Telegram token configured")
    
    logger.info("🌸 Kiyomi is starting up (threaded mode)...")
    
    async def _run():
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("🌸 Kiyomi is running! Waiting for messages...")
        # Block forever (until thread is killed)
        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run())


if __name__ == "__main__":
    main()
