"""Kiyomi Voice Reply — Respond with voice messages using ElevenLabs TTS.

Makes Kiyomi feel like a REAL personal assistant by speaking back.
No other AI bot does this naturally in Telegram.

Usage:
    from voice_reply import generate_voice_reply, should_use_voice
    
    if should_use_voice(user_message, config):
        audio_path = await generate_voice_reply(text, config)
        if audio_path:
            await update.message.reply_voice(voice=open(audio_path, "rb"))
"""

import asyncio
import hashlib
import logging
import urllib.request
import json
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kiyomi.voice")

VOICE_CACHE_DIR = Path.home() / ".kiyomi" / "voice_cache"


def _get_elevenlabs_key(config: dict) -> Optional[str]:
    """Get ElevenLabs API key from config."""
    return config.get("elevenlabs_key", "") or config.get("elevenlabs", {}).get("api_key", "")


def _get_voice_id(config: dict) -> str:
    """Get the configured voice ID, or use a pleasant default."""
    voice_cfg = config.get("voice", {})
    return voice_cfg.get("voice_id", "") or config.get("elevenlabs_voice_id", "") or "21m00Tcm4TlvDq8ikWAM"  # Rachel (default)


async def generate_voice_reply(
    text: str,
    config: dict,
    voice_id: Optional[str] = None,
    model: str = "eleven_turbo_v2_5",
) -> Optional[Path]:
    """Generate a voice message from text using ElevenLabs.
    
    Returns path to .ogg file, or None if TTS fails/not configured.
    """
    api_key = _get_elevenlabs_key(config)
    if not api_key:
        logger.debug("No ElevenLabs key configured — skipping voice reply")
        return None
    
    voice_id = voice_id or _get_voice_id(config)
    
    # Clean text for speech (remove markdown, emojis that don't speak well)
    clean_text = _clean_for_speech(text)
    if not clean_text or len(clean_text) < 5:
        return None
    
    # Truncate very long responses (ElevenLabs has limits + costs money)
    if len(clean_text) > 2000:
        clean_text = clean_text[:2000] + "... and that's the summary."
    
    # Check cache (save API calls for repeated phrases)
    cache_key = hashlib.md5(f"{voice_id}:{clean_text}".encode()).hexdigest()
    VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = VOICE_CACHE_DIR / f"{cache_key}.mp3"
    if cached.exists():
        logger.debug(f"Voice cache hit: {cache_key}")
        return cached
    
    try:
        # Call ElevenLabs API
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        
        payload = json.dumps({
            "text": clean_text,
            "model_id": model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.3,
                "use_speaker_boost": True,
            },
        }).encode("utf-8")
        
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            method="POST",
        )
        
        # Run in thread to not block
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=30))
        
        audio_data = response.read()
        if len(audio_data) < 100:
            logger.error("ElevenLabs returned too-small audio")
            return None
        
        # Save to cache
        cached.write_bytes(audio_data)
        logger.info(f"Voice generated: {len(audio_data)} bytes, cached as {cache_key}")
        
        return cached
    
    except Exception as e:
        logger.error(f"ElevenLabs TTS failed: {e}")
        return None


def should_use_voice(user_message: str, config: dict) -> bool:
    """Decide if Kiyomi should reply with voice.
    
    Triggers:
    - User sent a voice message (handled separately in bot.py)
    - User explicitly asks for voice: "say that", "tell me", "read this"
    - Voice mode is enabled in config
    - Morning/evening brief (configured)
    """
    # Check if voice is enabled at all
    voice_cfg = config.get("voice", {})
    if not voice_cfg.get("enabled", False):
        return False
    
    # Check if ElevenLabs key exists
    if not _get_elevenlabs_key(config):
        return False
    
    msg_lower = user_message.lower().strip()
    
    # Explicit voice requests
    voice_triggers = [
        "say that",
        "say it",
        "tell me",
        "read this",
        "read it",
        "speak",
        "voice",
        "out loud",
        "audio",
        "listen",
    ]
    if any(trigger in msg_lower for trigger in voice_triggers):
        return True
    
    # Auto-voice mode (reply to voice with voice)
    if voice_cfg.get("auto_reply_voice", False):
        return True
    
    return False


def _clean_for_speech(text: str) -> str:
    """Clean text for natural speech output."""
    import re
    
    # Remove markdown formatting (order matters: code blocks before inline code)
    text = re.sub(r'```[\w]*\n[\s\S]*?```', '', text)  # ```lang\ncode\n```
    text = re.sub(r'```[\s\S]*?```', '', text)          # ```code```
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)       # **bold**
    text = re.sub(r'\*(.+?)\*', r'\1', text)            # *italic*
    text = re.sub(r'`([^`]+)`', r'\1', text)            # `code`
    text = re.sub(r'#{1,6}\s*', '', text)           # headers
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text) # links
    
    # Remove emoji that don't speak well (keep some)
    # Keep: ❤️ 👍 😊 etc. Remove: 📊 🔍 💰 📋 etc.
    text = re.sub(r'[📊🔍💰📋🏦💵💳📈📉🔗⬜✅❌🔧🤖📄⏰💊🌅]', '', text)
    
    # Remove bullet points and list markers
    text = re.sub(r'^[\s]*[•\-\*]\s*', '', text, flags=re.MULTILINE)
    
    # Remove URLs
    text = re.sub(r'https?://\S+', '', text)
    
    # Clean up whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    
    return text


def get_voice_stats(config: dict) -> dict:
    """Get voice usage stats."""
    if not VOICE_CACHE_DIR.exists():
        return {"cached_files": 0, "cache_size_mb": 0}
    
    files = list(VOICE_CACHE_DIR.glob("*.mp3"))
    total_size = sum(f.stat().st_size for f in files)
    
    return {
        "cached_files": len(files),
        "cache_size_mb": round(total_size / (1024 * 1024), 2),
    }


def clear_voice_cache() -> str:
    """Clear the voice cache."""
    if not VOICE_CACHE_DIR.exists():
        return "No voice cache to clear."
    
    files = list(VOICE_CACHE_DIR.glob("*.mp3"))
    for f in files:
        f.unlink()
    
    return f"Cleared {len(files)} cached voice files."
