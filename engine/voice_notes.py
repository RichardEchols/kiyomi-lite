"""
Kiyomi Lite — Voice Notes to Action Items
Transform voice messages into structured action items: reminders, tasks, events, and facts.

Core functions:
- transcribe_voice() — OpenAI Whisper API transcription
- extract_action_items() — Parse transcript for actionable items
- process_voice_note() — Full workflow from audio to structured data
- handle_voice_message() — Telegram integration helper
"""

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from engine.config import load_config
from engine.memory import extract_facts_from_message, save_fact
from engine.reminders import parse_reminder_from_message, add_reminder

logger = logging.getLogger(__name__)


async def transcribe_voice(file_path: str) -> str:
    """
    Transcribe audio file using OpenAI Whisper API.
    
    Args:
        file_path: Path to audio file
        
    Returns:
        Transcribed text or error message
    """
    try:
        # Import OpenAI here to avoid startup dependency
        import openai
        
        config = load_config()
        api_key = config.get("openai_key", "")
        
        if not api_key:
            return "Error: OpenAI API key not configured"
        
        # Check if file exists
        if not Path(file_path).exists():
            return f"Error: Audio file not found: {file_path}"
        
        # Initialize OpenAI client
        client = openai.OpenAI(api_key=api_key)
        
        # Open audio file and transcribe
        with open(file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        
        return transcript.text.strip() if transcript.text else ""
        
    except ImportError:
        return "Error: OpenAI package not installed. Run: pip install openai"
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return f"Error: Transcription failed - {str(e)[:200]}"


def extract_action_items(text: str) -> List[Dict]:
    """
    Extract action items from transcribed text.
    
    Looks for:
    - Reminders: "remind me to...", "don't forget to..."
    - Tasks: "I need to...", "have to...", "should..."
    - Events: "meeting on...", "appointment at..."
    - Facts: "my new number is...", "I moved to..."
    
    Args:
        text: Transcribed text
        
    Returns:
        List of action items with type, text, and raw content
    """
    if not text:
        return []
    
    items = []
    text_lower = text.lower()
    
    # Reminder patterns
    reminder_patterns = [
        r"remind me (?:to )?(.+?)(?:\.|$|,| and | but)",
        r"don'?t (?:let me )?forget (?:to )?(.+?)(?:\.|$|,| and | but)",
        r"remember (?:to )?(.+?)(?:\.|$|,| and | but)",
        r"(?:make sure|ensure) (?:I|that I) (?:remember to |don't forget to )?(.+?)(?:\.|$|,| and | but)"
    ]
    
    for pattern in reminder_patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            item_text = match.group(1).strip()
            if len(item_text) > 3:  # Skip very short matches
                items.append({
                    "type": "reminder",
                    "text": item_text,
                    "raw": match.group(0)
                })
    
    # Task patterns
    task_patterns = [
        r"I (?:need|have|got) to (.+?)(?:\.|$|,| and | but)",
        r"I should (.+?)(?:\.|$|,| and | but)",
        r"I (?:gotta|must) (.+?)(?:\.|$|,| and | but)",
        r"(?:todo|to do|task):?\s*(.+?)(?:\.|$|,| and | but)"
    ]
    
    for pattern in task_patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            item_text = match.group(1).strip()
            if len(item_text) > 3:
                items.append({
                    "type": "task", 
                    "text": item_text,
                    "raw": match.group(0)
                })
    
    # Event patterns  
    event_patterns = [
        r"(?:meeting|appointment|call) (?:on|at|with) (.+?)(?:\.|$|,| and | but)",
        r"(?:have|got) (?:a |an )?(?:meeting|appointment|interview|call|date) (.+?)(?:\.|$|,| and | but)",
        r"scheduled (?:for|on|at) (.+?)(?:\.|$|,| and | but)",
        r"(?:conference|presentation|webinar) (?:on|at) (.+?)(?:\.|$|,| and | but)"
    ]
    
    for pattern in event_patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            item_text = match.group(1).strip()
            if len(item_text) > 3:
                items.append({
                    "type": "event",
                    "text": item_text, 
                    "raw": match.group(0)
                })
    
    # Fact patterns (personal information)
    fact_patterns = [
        r"my (?:new |current )?(?:phone|number|cell) (?:is |number is )?(.+?)(?:\.|$|,| and | but)",
        r"my (?:new |current )?(?:email|address) (?:is )?(.+?)(?:\.|$|,| and | but)",
        r"I (?:moved|relocated) (?:to )?(.+?)(?:\.|$|,| and | but)",
        r"my (?:new |current )?address (?:is )?(.+?)(?:\.|$|,| and | but)",
        r"(?:case|file|reference) number (?:is )?(.+?)(?:\.|$|,| and | but)",
        r"password (?:is |for .+ is )?(.+?)(?:\.|$|,| and | but)"
    ]
    
    for pattern in fact_patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            item_text = match.group(1).strip()
            if len(item_text) > 2:
                items.append({
                    "type": "fact",
                    "text": item_text,
                    "raw": match.group(0)
                })
    
    # Remove duplicates based on similar text
    unique_items = []
    for item in items:
        is_duplicate = False
        for existing in unique_items:
            # Check if items are very similar (avoid duplicates from overlapping patterns)
            if (item["type"] == existing["type"] and 
                abs(len(item["text"]) - len(existing["text"])) < 5 and
                item["text"][:20].lower() == existing["text"][:20].lower()):
                is_duplicate = True
                break
        if not is_duplicate:
            unique_items.append(item)
    
    return unique_items


async def process_voice_note(file_path: str) -> Dict:
    """
    Full voice note processing workflow.
    
    Args:
        file_path: Path to audio file
        
    Returns:
        Dict with transcript, action_items, and summary
    """
    # Step 1: Transcribe audio
    transcript = await transcribe_voice(file_path)
    
    if transcript.startswith("Error:"):
        return {
            "transcript": transcript,
            "action_items": [],
            "summary": transcript
        }
    
    # Step 2: Extract action items
    action_items = extract_action_items(transcript)
    
    # Step 3: Process action items through existing systems
    processed_count = 0
    
    for item in action_items:
        try:
            if item["type"] == "reminder":
                # Try to parse as a reminder using existing system
                reminder_info = parse_reminder_from_message(f"remind me to {item['text']}")
                if reminder_info:
                    add_reminder(
                        reminder_info["text"],
                        reminder_info["time"], 
                        reminder_info["recurring"]
                    )
                    processed_count += 1
                    
            elif item["type"] == "fact":
                # Extract and save facts using existing system
                facts = extract_facts_from_message(item["raw"])
                for fact, category in facts:
                    save_fact(fact, category)
                    processed_count += 1
                    
        except Exception as e:
            logger.error(f"Failed to process {item['type']}: {e}")
    
    # Step 4: Generate summary
    if not action_items:
        summary = "Voice note transcribed, but no action items detected."
    else:
        item_counts = {}
        for item in action_items:
            item_counts[item["type"]] = item_counts.get(item["type"], 0) + 1
        
        count_parts = []
        for item_type, count in item_counts.items():
            count_parts.append(f"{count} {item_type}{'s' if count > 1 else ''}")
        
        summary = f"Found {len(action_items)} action items: {', '.join(count_parts)}"
        if processed_count > 0:
            summary += f" ({processed_count} processed successfully)"
    
    return {
        "transcript": transcript,
        "action_items": action_items,
        "summary": summary
    }


async def handle_voice_message(update, context) -> str:
    """
    Handle voice message from Telegram bot.
    
    Downloads voice file, processes it, and returns formatted response.
    
    Args:
        update: Telegram Update object
        context: Telegram Context object
        
    Returns:
        Formatted response string
    """
    try:
        # Get voice file from message
        voice = update.message.voice or update.message.audio
        if not voice:
            return "No voice message found."
        
        # Download voice file
        voice_file = await voice.get_file()
        
        # Create temp directory if needed
        temp_dir = Path.home() / ".kiyomi" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Download to temporary file
        voice_path = temp_dir / f"voice_{voice.file_id}.ogg"
        await voice_file.download_to_drive(voice_path)
        
        # Process voice note
        result = await process_voice_note(str(voice_path))
        
        # Format response
        response_parts = ["🎤 Voice Note Processed!"]
        
        # Add transcript (truncated if long)
        transcript = result["transcript"]
        if len(transcript) > 200:
            transcript = transcript[:200] + "..."
        response_parts.append(f"\n📝 Transcript: \"{transcript}\"")
        
        # Add action items
        action_items = result["action_items"]
        if action_items:
            response_parts.append(f"\n✅ Found {len(action_items)} action items:")
            
            for item in action_items[:5]:  # Limit to first 5 items
                emoji_map = {
                    "reminder": "📌",
                    "task": "📋", 
                    "event": "📅",
                    "fact": "💡"
                }
                emoji = emoji_map.get(item["type"], "•")
                item_text = item["text"]
                if len(item_text) > 60:
                    item_text = item_text[:60] + "..."
                response_parts.append(f"- {emoji} {item['type'].title()}: {item_text}")
            
            if len(action_items) > 5:
                response_parts.append(f"- ... and {len(action_items) - 5} more items")
        else:
            response_parts.append("\n📝 No action items detected - just saved the transcript.")
        
        # Cleanup temporary file
        try:
            voice_path.unlink(missing_ok=True)
        except Exception:
            pass
        
        return "\n".join(response_parts)
        
    except Exception as e:
        logger.error(f"Voice message handling failed: {e}")
        return f"Sorry, I couldn't process that voice message. Error: {str(e)[:100]}"