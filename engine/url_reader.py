"""
Kiyomi Lite — URL Reader
Fetches web pages and extracts readable text.
When user sends a link, Kiyomi reads it.
"""
import re
import logging
import urllib.request
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# Regex to find URLs in text
URL_PATTERN = re.compile(
    r'https?://[^\s<>"\')\]]+',
    re.IGNORECASE
)

# Tags whose content we skip (scripts, styles, etc.)
_SKIP_TAGS = {'script', 'style', 'noscript', 'svg', 'head', 'nav', 'footer', 'header'}
_BLOCK_TAGS = {'p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'article', 'section'}


class _TextExtractor(HTMLParser):
    """Simple HTML-to-text extractor."""
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag in _BLOCK_TAGS:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag in _BLOCK_TAGS:
            self.parts.append('\n')

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)

    def get_text(self) -> str:
        raw = ''.join(self.parts)
        # Strip each line but keep blank lines as paragraph separators
        lines = [line.strip() for line in raw.splitlines()]
        # Collapse consecutive blank lines into one, remove leading/trailing blanks
        result: list[str] = []
        prev_blank = True  # treat start as blank to skip leading empties
        for line in lines:
            if line:
                result.append(line)
                prev_blank = False
            elif not prev_blank:
                result.append('')  # keep one blank line between paragraphs
                prev_blank = True
        # Remove trailing blank if present
        if result and result[-1] == '':
            result.pop()
        return '\n'.join(result)


def find_urls(text: str) -> list[str]:
    """Extract URLs from a message."""
    return URL_PATTERN.findall(text)


def _fetch_direct(url: str, max_chars: int = 3000, timeout: int = 10) -> str | None:
    """Fetch URL directly with urllib (works for static HTML pages)."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,*/*',
        })
        resp = urllib.request.urlopen(req, timeout=timeout)
        
        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' not in content_type and 'text/plain' not in content_type:
            return None
        
        raw = resp.read(512_000)
        
        charset = 'utf-8'
        if 'charset=' in content_type:
            charset = content_type.split('charset=')[-1].split(';')[0].strip()
        html = raw.decode(charset, errors='replace')
        
        extractor = _TextExtractor()
        extractor.feed(html)
        text = extractor.get_text()
        
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else ''
        
        if not text or len(text) < 50:
            return None
        
        if len(text) > max_chars:
            text = text[:max_chars] + '...'
        
        result = ''
        if title:
            result = f"**{title}**\n\n"
        result += text
        return result
        
    except Exception as e:
        logger.warning(f"Direct fetch failed for {url}: {e}")
        return None


def _fetch_via_jina(url: str, max_chars: int = 3000, timeout: int = 15) -> str | None:
    """Fetch URL via Jina Reader API — renders JavaScript, works for dynamic pages."""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(jina_url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'text/plain',
            'X-No-Cache': 'true',
        })
        resp = urllib.request.urlopen(req, timeout=timeout)
        text = resp.read(max_chars + 500).decode('utf-8', errors='replace')
        
        if not text or len(text) < 50:
            return None
        if 'Just a moment...' in text[:200]:
            return None  # Cloudflare blocked
            
        if len(text) > max_chars:
            text = text[:max_chars] + '...'
        return text
        
    except Exception as e:
        logger.warning(f"Jina fetch failed for {url}: {e}")
        return None


def fetch_url(url: str, max_chars: int = 3000, timeout: int = 10) -> str | None:
    """Fetch a URL and return readable text content.
    
    Tries direct fetch first (fast, no deps). Falls back to Jina Reader
    for JS-heavy pages (LinkedIn, Indeed, SPAs).
    """
    if not url or not url.strip():
        return None
    
    # Try direct fetch first (faster)
    result = _fetch_direct(url, max_chars, timeout)
    
    # If direct fetch got very little content, try Jina (handles JS pages)
    if result is None or len(result) < 100:
        logger.info(f"Direct fetch insufficient, trying Jina Reader for {url}")
        jina_result = _fetch_via_jina(url, max_chars)
        if jina_result and len(jina_result) > len(result or ''):
            return jina_result
    
    return result


def _extract_paragraphs(text: str, max_paragraphs: int = 12, min_len: int = 40) -> list[str]:
    """Extract clean, numbered paragraph-like chunks from text."""
    # Normalize whitespace
    normalized = re.sub(r'\r\n?', '\n', text)
    normalized = re.sub(r'[ \t]+', ' ', normalized)
    # Split on blank lines
    raw_paras = [p.strip() for p in re.split(r'\n\s*\n', normalized) if p.strip()]

    cleaned: list[str] = []
    for para in raw_paras:
        # Drop obvious boilerplate / tiny fragments
        if len(para) < min_len:
            continue
        # Remove repeated nav-like lines
        para = re.sub(r'\s*•\s*', ' ', para).strip()
        cleaned.append(para)
        if len(cleaned) >= max_paragraphs:
            break

    return cleaned


def _format_numbered_paragraphs(text: str, max_paragraphs: int = 12) -> str:
    """Return numbered paragraphs for precise Q&A."""
    paras = _extract_paragraphs(text, max_paragraphs=max_paragraphs)
    if not paras:
        return text
    lines = [f"Paragraph {i + 1}: {p}" for i, p in enumerate(paras)]
    return "\n\n".join(lines)


def read_urls_in_message(message: str) -> str:
    """Find URLs in a message, fetch them, and return context for the AI.
    
    Returns empty string if no URLs or all fetches fail.
    """
    urls = find_urls(message)
    if not urls:
        return ''

    want_paragraphs = bool(re.search(r'\bparagraphs?\b|\bparas?\b|¶', message, re.IGNORECASE))
    
    # Limit to 3 URLs per message
    contexts = []
    for url in urls[:3]:
        content = fetch_url(url, max_chars=8000 if want_paragraphs else 3000)
        if content:
            if want_paragraphs:
                numbered = _format_numbered_paragraphs(content, max_paragraphs=12)
                contexts.append(f"[Numbered paragraphs from {url}]\n{numbered}")
            else:
                contexts.append(f"[Content from {url}]\n{content}")
    
    if not contexts:
        return ''
    
    return '\n\n---\n\n'.join(contexts)
