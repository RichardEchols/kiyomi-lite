"""
Kiyomi Image Generation — AI-powered image creation
Generates images through available AI tools and returns file paths for Telegram.
"""
import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Image request keywords/patterns
IMAGE_KEYWORDS = [
    "make me an image", "make me a picture", "make an image", "make a picture",
    "generate an image", "generate a picture", "generate image", "generate picture",
    "create an image", "create a picture", "create image", "create picture", 
    "draw me", "draw an", "draw a picture", "draw an image",
    "design a", "design an image", "design a picture",
    "paint me", "paint an", "paint a picture", "paint an image",
    "sketch me", "sketch an", "sketch a picture", "sketch an image",
    "show me a picture of", "show me an image of",
    "visualize", "render an image", "render a picture"
]

# Additional patterns (regex)
IMAGE_PATTERNS = [
    r"\b(make|create|generate|draw|design|paint|sketch|show|render)\s+(me\s+)?(a|an|some)?\s*(picture|image|drawing|sketch|painting|artwork|visual|graphic)",
    r"\bimage\s+of\b",
    r"\bpicture\s+of\b",
    r"\bdraw\s+me\s+",
    r"\bshow\s+me\s+(a|an)\s+(picture|image)",
]


def is_image_request(message: str) -> bool:
    """
    Detect if user message is requesting image generation.
    
    Args:
        message: User's message text
        
    Returns:
        bool: True if this appears to be an image generation request
    """
    if not message or not isinstance(message, str):
        return False
    
    message_lower = message.lower().strip()
    
    # Check exact keyword matches
    for keyword in IMAGE_KEYWORDS:
        if keyword in message_lower:
            logger.info(f"Image request detected (keyword): '{keyword}' in '{message[:50]}...'")
            return True
    
    # Check regex patterns
    for pattern in IMAGE_PATTERNS:
        if re.search(pattern, message_lower):
            logger.info(f"Image request detected (pattern): '{pattern}' in '{message[:50]}...'")
            return True
    
    # Check for common AI art style mentions
    art_styles = [
        "photorealistic", "cartoon", "anime", "digital art", "oil painting",
        "watercolor", "sketch style", "realistic", "abstract", "minimalist",
        "cyberpunk", "steampunk", "fantasy art", "concept art"
    ]
    
    for style in art_styles:
        if style in message_lower and any(word in message_lower for word in ["style", "art", "image", "picture"]):
            logger.info(f"Image request detected (art style): '{style}' in '{message[:50]}...'")
            return True
    
    return False


async def _generate_via_gemini_api(prompt: str) -> Optional[str]:
    """
    Generate image using Google Gemini API (google.genai SDK).
    
    Args:
        prompt: Image generation prompt
        
    Returns:
        str: Path to generated image file, or None if failed
    """
    try:
        from google import genai
        from google.genai import types
        
        api_key = "AIzaSyCH9Ps-m977k-jt15bbg5Q6R_YQSLbnAyU"
        client = genai.Client(api_key=api_key)
        
        logger.info(f"Generating image via Gemini API: '{prompt[:100]}...'")
        
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.0-flash-exp-image-generation",
            contents=f"Generate a high-quality image: {prompt}",
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"]
            )
        )
        
        # Extract image from response parts
        if response and response.candidates:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    temp_dir = Path(tempfile.gettempdir()) / "kiyomi_images"
                    temp_dir.mkdir(exist_ok=True)
                    
                    timestamp = int(time.time())
                    ext = part.inline_data.mime_type.split("/")[-1]
                    filename = f"gemini_image_{timestamp}.{ext}"
                    file_path = temp_dir / filename
                    
                    with open(file_path, "wb") as f:
                        f.write(part.inline_data.data)
                    
                    logger.info(f"Gemini API image saved to: {file_path}")
                    return str(file_path)
        
        logger.warning("Gemini API response did not contain image data")
        return None
            
    except ImportError:
        logger.warning("google.genai not available for Gemini API image generation")
        return None
    except Exception as e:
        logger.error(f"Gemini API image generation failed: {e}")
        return None


async def _generate_via_gemini_cli(prompt: str) -> Optional[str]:
    """
    Generate image using Gemini CLI.
    
    Args:
        prompt: Image generation prompt
        
    Returns:
        str: Path to generated image file, or None if failed
    """
    try:
        gemini_path = shutil.which("gemini")
        if not gemini_path:
            logger.info("Gemini CLI not found")
            return None
        
        logger.info(f"Generating image via Gemini CLI: '{prompt[:100]}...'")
        
        # Create temp directory for output
        temp_dir = Path(tempfile.gettempdir()) / "kiyomi_images"
        temp_dir.mkdir(exist_ok=True)
        
        # Execute Gemini CLI with image generation prompt
        full_prompt = f"Generate a detailed, high-quality image: {prompt}"
        
        proc = await asyncio.create_subprocess_exec(
            "gemini", "-p", full_prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(temp_dir)
        )
        
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        
        if proc.returncode == 0:
            # Look for generated image files in temp directory
            # Gemini CLI might save images with various names
            image_extensions = [".png", ".jpg", ".jpeg", ".webp"]
            
            for ext in image_extensions:
                for file_path in temp_dir.glob(f"*{ext}"):
                    if file_path.stat().st_mtime > time.time() - 120:  # Created in last 2 minutes
                        logger.info(f"Gemini CLI image found: {file_path}")
                        return str(file_path)
            
            # If no image files found, CLI might have output text instead
            logger.warning("Gemini CLI completed but no image file found")
            return None
        else:
            error_msg = stderr.decode('utf-8', errors='replace')
            logger.error(f"Gemini CLI failed: {error_msg}")
            return None
    
    except asyncio.TimeoutError:
        logger.error("Gemini CLI image generation timed out")
        return None
    except Exception as e:
        logger.error(f"Gemini CLI image generation failed: {e}")
        return None


async def _generate_via_fal_ai(prompt: str) -> Optional[str]:
    """
    Generate image using Fal AI API.
    
    Args:
        prompt: Image generation prompt
        
    Returns:
        str: Path to generated image file, or None if failed
    """
    try:
        import fal_client
        import requests
        
        # Set API key from TOOLS.md
        os.environ["FAL_KEY"] = "266da53d-60dd-4a35-ad18-19ea76131d85:d6f64598ff394a6cd1be12bb44cc30c1"
        
        logger.info(f"Generating image via Fal AI: '{prompt[:100]}...'")
        
        # Use Fal AI's flux model (or another available model)
        def generate_image():
            handler = fal_client.submit(
                "fal-ai/flux/schnell",
                arguments={
                    "prompt": prompt,
                    "num_images": 1,
                    "image_size": "landscape_4_3",  # or "square_hd", "portrait_4_3"
                    "num_inference_steps": 4,
                    "enable_safety_checker": True
                }
            )
            
            result = handler.get()
            return result
        
        # Run in thread since fal_client is sync
        result = await asyncio.to_thread(generate_image)
        
        if result and "images" in result and result["images"]:
            image_url = result["images"][0]["url"]
            
            # Download the image
            temp_dir = Path(tempfile.gettempdir()) / "kiyomi_images"
            temp_dir.mkdir(exist_ok=True)
            
            timestamp = int(time.time())
            filename = f"fal_ai_image_{timestamp}.png"
            file_path = temp_dir / filename
            
            response = requests.get(image_url, timeout=30)
            response.raise_for_status()
            
            with open(file_path, "wb") as f:
                f.write(response.content)
            
            logger.info(f"Fal AI image saved to: {file_path}")
            return str(file_path)
        else:
            logger.warning("Fal AI did not return image data")
            return None
    
    except ImportError:
        logger.warning("fal_client not available for Fal AI image generation")
        return None
    except Exception as e:
        logger.error(f"Fal AI image generation failed: {e}")
        return None


async def generate_image(prompt: str, provider: str = "auto") -> str:
    """
    Generate an image using the best available provider.
    
    Args:
        prompt: Image generation prompt/description
        provider: Specific provider to use ("gemini_cli", "gemini_api", "fal_ai", "auto")
        
    Returns:
        str: Path to generated image file, or error message
    """
    if not prompt or not prompt.strip():
        return "No image prompt provided."
    
    # Clean up prompt
    clean_prompt = prompt.strip()
    
    # Remove common prefixes from prompt
    prefixes_to_remove = [
        "make me an image of", "make me a picture of", "generate an image of",
        "generate a picture of", "create an image of", "create a picture of",
        "draw me", "draw an image of", "draw a picture of", "show me a picture of",
        "make me an image", "make me a picture", "generate an image", "generate a picture"
    ]
    
    for prefix in prefixes_to_remove:
        if clean_prompt.lower().startswith(prefix):
            clean_prompt = clean_prompt[len(prefix):].strip()
            break
    
    logger.info(f"Generating image with cleaned prompt: '{clean_prompt}'")
    
    # Try providers in order based on priority or specific request
    if provider == "auto":
        providers_to_try = ["gemini_cli", "fal_ai", "gemini_api"]
    elif provider == "gemini_cli":
        providers_to_try = ["gemini_cli"]
    elif provider == "gemini_api":
        providers_to_try = ["gemini_api"]
    elif provider == "fal_ai":
        providers_to_try = ["fal_ai"]
    else:
        return f"Unknown image generation provider: {provider}"
    
    for provider_name in providers_to_try:
        try:
            if provider_name == "gemini_cli":
                result = await _generate_via_gemini_cli(clean_prompt)
            elif provider_name == "gemini_api":
                result = await _generate_via_gemini_api(clean_prompt)
            elif provider_name == "fal_ai":
                result = await _generate_via_fal_ai(clean_prompt)
            else:
                continue
            
            if result and Path(result).exists():
                logger.info(f"Image generated successfully via {provider_name}: {result}")
                return result
            
        except Exception as e:
            logger.error(f"Error with {provider_name}: {e}")
            continue
    
    # If all providers failed
    error_msg = (
        f"Failed to generate image with prompt: '{clean_prompt[:100]}...'\n\n"
        "Tried all available providers. This could be due to:\n"
        "• Network connectivity issues\n"
        "• API rate limits or quota exceeded\n"
        "• CLI tools not installed/authenticated\n"
        "• Content policy restrictions\n\n"
        "Try a different prompt or check your setup."
    )
    
    logger.error(error_msg)
    return error_msg


# Utility functions

def get_available_providers() -> list:
    """Get list of available image generation providers."""
    providers = []
    
    # Check Gemini CLI
    if shutil.which("gemini"):
        providers.append("gemini_cli")
    
    # Check Fal AI
    try:
        import fal_client
        providers.append("fal_ai")
    except ImportError:
        pass
    
    # Check Gemini API
    try:
        import google.generativeai
        providers.append("gemini_api")
    except ImportError:
        pass
    
    return providers


def cleanup_old_images(max_age_hours: int = 24):
    """
    Clean up old generated images from temp directory.
    
    Args:
        max_age_hours: Maximum age in hours before cleanup
    """
    try:
        temp_dir = Path(tempfile.gettempdir()) / "kiyomi_images"
        if not temp_dir.exists():
            return
        
        cutoff_time = time.time() - (max_age_hours * 3600)
        
        for image_file in temp_dir.glob("*"):
            if image_file.is_file() and image_file.stat().st_mtime < cutoff_time:
                try:
                    image_file.unlink()
                    logger.debug(f"Cleaned up old image: {image_file}")
                except Exception as e:
                    logger.warning(f"Could not clean up {image_file}: {e}")
    
    except Exception as e:
        logger.error(f"Error during image cleanup: {e}")


def get_generation_stats() -> dict:
    """Get stats about image generation usage."""
    try:
        temp_dir = Path(tempfile.gettempdir()) / "kiyomi_images"
        if not temp_dir.exists():
            return {"total_images": 0, "total_size_mb": 0}
        
        total_files = 0
        total_size = 0
        
        for image_file in temp_dir.glob("*"):
            if image_file.is_file():
                total_files += 1
                total_size += image_file.stat().st_size
        
        return {
            "total_images": total_files,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "available_providers": get_available_providers()
        }
    
    except Exception as e:
        logger.error(f"Error getting generation stats: {e}")
        return {"error": str(e)}