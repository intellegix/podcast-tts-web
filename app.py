"""
Podcast TTS Web App
Converts podcast scripts to audio using OpenAI TTS API

Enterprise-grade implementation with:
- Security headers and CSRF protection
- Rate limiting on sensitive endpoints
- Retry logic with exponential backoff
- Circuit breakers for external APIs
- Structured logging
- Automatic cleanup scheduling
"""

# Gevent monkey patching - MUST be before all other imports
from gevent import monkey
monkey.patch_all()

import os
import re
import json
import uuid
import time
import tempfile
import shutil
import logging
import atexit
from datetime import datetime
from pathlib import Path
from functools import wraps
from collections import deque
from flask import Flask, render_template, request, jsonify, send_file, Response, session, redirect, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from openai import OpenAI
import openai
import anthropic
import requests
import httpx
import sys
import gevent
from gevent.pool import Pool as GeventPool
from gevent.lock import RLock
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from apscheduler.schedulers.background import BackgroundScheduler
from job_store import job_store, Job, JobStatus, Stage, StageResult

# Configure logging (structured format)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())

# Secure session cookies configuration
app.config.update(
    SESSION_COOKIE_SECURE=os.environ.get('FLASK_ENV') == 'production',  # True in production
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Strict'
)

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# Configuration - API keys must be set via environment variables
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
CLAUDE_API_KEY = os.environ.get('CLAUDE_API_KEY')
PERPLEXITY_API_KEY = os.environ.get('PERPLEXITY_API_KEY')
APP_PASSWORD = os.environ.get('PASSWORD', '')
TEMP_DIR = Path(tempfile.gettempdir()) / 'podcast-tts'
TEMP_DIR.mkdir(exist_ok=True)

# Input size limits (security)
MAX_TEXT_LENGTH = 5 * 1024 * 1024  # 5MB max text input
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB max file upload
MAX_CHUNKS = 1000  # Maximum chunks per job
ALLOWED_FILE_EXTENSIONS = {'.txt', '.md', '.text'}

# OpenAI TTS options
VOICES = ['nova', 'alloy', 'echo', 'fable', 'onyx', 'shimmer']
MODELS = ['tts-1-hd', 'tts-1']

# Multi-voice speaker detection - gender-based voice mapping
VOICE_MAP = {
    'male': 'echo',
    'female': 'shimmer',
    'neutral': 'alloy'
}

# Common names for gender detection (first names only, lowercase)
MALE_NAMES = {
    'alex', 'john', 'mike', 'michael', 'david', 'james', 'robert', 'chris', 'christopher',
    'daniel', 'matthew', 'andrew', 'josh', 'joshua', 'ryan', 'brandon', 'jason', 'justin',
    'brian', 'kevin', 'eric', 'steve', 'steven', 'mark', 'paul', 'adam', 'scott', 'greg',
    'jeff', 'jeffrey', 'tim', 'timothy', 'tom', 'thomas', 'joe', 'joseph', 'nick', 'nicholas',
    'tony', 'anthony', 'ben', 'benjamin', 'sam', 'samuel', 'jake', 'jacob', 'ethan', 'noah',
    'william', 'bill', 'richard', 'rick', 'charles', 'charlie', 'george', 'peter', 'patrick',
    'sean', 'kyle', 'tyler', 'aaron', 'nathan', 'jordan', 'dylan', 'luke', 'evan', 'austin',
    'host', 'narrator', 'announcer'  # Generic male-coded roles
}

FEMALE_NAMES = {
    'sarah', 'emma', 'lisa', 'mary', 'jennifer', 'amanda', 'jessica', 'ashley', 'emily',
    'elizabeth', 'megan', 'lauren', 'rachel', 'stephanie', 'nicole', 'heather', 'michelle',
    'amber', 'melissa', 'tiffany', 'christina', 'rebecca', 'laura', 'danielle', 'brittany',
    'kimberly', 'kelly', 'crystal', 'amy', 'angela', 'andrea', 'anna', 'hannah', 'samantha',
    'katherine', 'kate', 'katie', 'karen', 'nancy', 'betty', 'sandra', 'margaret', 'susan',
    'dorothy', 'patricia', 'linda', 'barbara', 'helen', 'maria', 'sophia', 'olivia', 'ava',
    'isabella', 'mia', 'charlotte', 'abigail', 'harper', 'evelyn', 'madison', 'grace', 'chloe',
    'victoria', 'natalie', 'julia', 'lily', 'claire', 'zoe', 'leah', 'audrey', 'maya', 'lucy',
    'hostess', 'co-host'  # Generic female-coded roles
}

# Parallel processing configuration
# Set to 0 for unlimited (1 agent per chunk), or a number to limit concurrent workers
MAX_CONCURRENT_CHUNKS = int(os.environ.get('TTS_MAX_CONCURRENT', '0'))  # 0 = unlimited (1 per chunk)
MAX_AI_CONCURRENT = int(os.environ.get('AI_MAX_CONCURRENT', '20'))  # Legacy fallback - enterprise default

# Enterprise parallelization - separate limits for each AI provider
MAX_PERPLEXITY_CONCURRENT = int(os.environ.get('PERPLEXITY_MAX_CONCURRENT', '20'))  # Parallel Perplexity agents
MAX_CLAUDE_CONCURRENT = int(os.environ.get('CLAUDE_MAX_CONCURRENT', '20'))  # Parallel Claude agents

# Claude model configuration (allows updating without code change)
# Primary: Claude 3.5 Sonnet (known working), Fallback: Claude 3 Haiku (faster)
CLAUDE_MODEL = os.environ.get('CLAUDE_MODEL', 'claude-3-5-sonnet-20241022')
CLAUDE_MODEL_FALLBACK = 'claude-3-haiku-20240307'

# ============== Multiplier Logic Configuration ==============
# Dynamic scaling based on estimated audio duration (1 agent per minute of audio)
MULTIPLIER_ENABLED = os.environ.get('MULTIPLIER_ENABLED', 'true').lower() == 'true'
MIN_AGENTS = int(os.environ.get('MIN_AGENTS', '5'))           # Minimum concurrent agents
MAX_AGENTS = int(os.environ.get('MAX_AGENTS', '100'))         # Maximum concurrent agents (hard cap)
AGENTS_PER_MINUTE = float(os.environ.get('AGENTS_PER_MINUTE', '1.0'))  # Scaling factor

# Batch processing thresholds for very long content
BATCH_THRESHOLD_MINUTES = int(os.environ.get('BATCH_THRESHOLD_MINUTES', '60'))  # Enable batching above this
MAX_EPISODES_PER_BATCH = int(os.environ.get('MAX_EPISODES_PER_BATCH', '10'))    # Episodes per batch
BATCH_COOLDOWN_SECONDS = int(os.environ.get('BATCH_COOLDOWN_SECONDS', '5'))     # Pause between batches

# Startup warnings for missing API keys (using logger)
if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY not set - TTS generation will fail")
if not CLAUDE_API_KEY:
    logger.warning("CLAUDE_API_KEY not set - Claude enhancement disabled")
if not PERPLEXITY_API_KEY:
    logger.warning("PERPLEXITY_API_KEY not set - Perplexity research disabled")


# Security headers middleware
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # CSP - allow self and inline styles/scripts for the simple UI
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
    # HSTS - only in production
    if os.environ.get('FLASK_ENV') == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# Request correlation ID middleware
@app.before_request
def add_request_id():
    """Add unique request ID for tracing"""
    request.id = request.headers.get('X-Request-ID', str(uuid.uuid4())[:8])


@app.after_request
def add_request_id_header(response):
    """Add request ID to response headers"""
    if hasattr(request, 'id'):
        response.headers['X-Request-ID'] = request.id
    return response


# Automatic cleanup scheduler
def cleanup_old_jobs():
    """Remove job directories older than 24 hours"""
    try:
        cutoff = time.time() - 86400  # 24 hours
        cleaned = 0
        for job_dir in TEMP_DIR.iterdir():
            if job_dir.is_dir():
                try:
                    if job_dir.stat().st_mtime < cutoff:
                        shutil.rmtree(job_dir, ignore_errors=True)
                        logger.info(f"Cleaned up old job: {job_dir.name}")
                        cleaned += 1
                except Exception as e:
                    logger.warning(f"Failed to clean job {job_dir.name}: {e}")
        if cleaned > 0:
            logger.info(f"Cleanup complete: removed {cleaned} old job directories")
    except Exception as e:
        logger.error(f"Cleanup scheduler error: {e}")


# Keep-alive ping to prevent Render free tier from sleeping
def keep_alive_ping():
    """Ping health endpoint to keep the service awake on Render free tier"""
    try:
        # Get the service URL from environment or use default
        service_url = os.environ.get('RENDER_EXTERNAL_URL', 'https://podcast-tts-web.onrender.com')
        health_url = f"{service_url}/health"

        response = requests.get(health_url, timeout=30)
        if response.status_code == 200:
            logger.info(f"Keep-alive ping successful: {response.status_code}")
        else:
            logger.warning(f"Keep-alive ping returned: {response.status_code}")
    except Exception as e:
        logger.warning(f"Keep-alive ping failed: {e}")


# Initialize background scheduler for cleanup and keep-alive (only in main process)
scheduler = BackgroundScheduler()
scheduler.add_job(cleanup_old_jobs, 'interval', hours=1)
scheduler.add_job(keep_alive_ping, 'interval', minutes=14)  # Ping every 14 min (Render sleeps at 15)
scheduler.start()
logger.info("Background scheduler started: cleanup (1h), keep-alive (14m)")
atexit.register(lambda: scheduler.shutdown(wait=False))

# Script expansion prompt for GPT-4o - Always creates two-person conversational podcast
SCRIPT_EXPANSION_PROMPT = """You are an expert podcast scriptwriter creating entertaining, engaging two-person dialogue.

SPEAKER REQUIREMENTS (STRICT):
- Create EXACTLY two speakers in every episode:
  - ALEX: The host/interviewer - introduces topics, asks thought-provoking questions, keeps conversation flowing
  - SARAH: The expert/analyst - provides detailed insights, data, examples, and expert commentary
- Use speaker names followed by colon (ALEX:, SARAH:)
- NEVER use any other speaker names - ONLY ALEX and SARAH

DIALOGUE STYLE:
- Create natural back-and-forth conversation (NOT monologues)
- Each speaker turn should be 1-4 sentences, then switch speakers
- ALEX asks questions, reacts with interest, provides transitions
- SARAH explains, provides examples, shares insights
- Include natural reactions: "That's fascinating!", "Great point!", "Let me add to that..."
- Add light humor and personality where appropriate
- Make it sound like a real conversation between friends who are experts

CONTENT REQUIREMENTS:
- Expand the outline into detailed, informative content
- Include specific facts, statistics, and examples from the outline
- Make technical content accessible and engaging
- Aim for 800-1200 words per episode section

FORMAT RULES:
- Do NOT include stage directions in parentheses like (laughs) or (pauses)
- Do NOT use headers or bullet points - just dialogue
- Start each line with speaker name: ALEX: or SARAH:

OUTPUT: Return ONLY the expanded dialogue script with ALEX and SARAH speakers, no explanations."""

# Script expansion model
SCRIPT_EXPANSION_MODEL = os.environ.get('SCRIPT_EXPANSION_MODEL', 'gpt-4o')

# Claude enhancement prompt for natural, engaging two-person dialogue
CLAUDE_ENHANCEMENT_PROMPT = """You are an expert podcast script editor. Your job is to polish dialogue for maximum listener engagement while preserving all content and meaning.

TWO-PERSON DIALOGUE BALANCE:
- Ensure natural back-and-forth between ALEX and SARAH
- ALEX asks questions, provides transitions, reacts with interest
- SARAH provides expert insights, examples, detailed explanations
- Neither speaker should dominate - aim for 40-60% balance
- Each speaker turn should be 1-4 sentences before switching
- Add engaging reactions: "That's fascinating!", "Great point!", "Tell me more about that..."

ENHANCEMENT RULES:
1. NATURAL FLOW: Make dialogue sound like real conversation, not scripted. Add filler words sparingly ("you know", "I mean", "right?")
2. PACING: Vary sentence length. Short punchy lines. Then longer explanatory ones. Create rhythm.
3. PERSONALITY: Add speaker quirks, callbacks to earlier points, genuine reactions
4. ENTERTAINMENT: Include subtle humor, relatable analogies, storytelling moments
5. TTS OPTIMIZATION (CRITICAL):
   - Write ALL numbers as words (fifty-eight thousand, not 58,000)
   - Spell out abbreviations on first use (Purchase Order, or P O, not PO)
   - Add natural pauses with punctuation (commas, ellipses, dashes)
   - Avoid tongue-twisters and awkward consonant clusters
   - Use contractions naturally (don't, won't, can't)
6. EMOTIONAL BEATS: Add moments of excitement, surprise, reflection
7. LISTENER HOOKS: Tease upcoming content, create curiosity gaps

PRESERVE:
- All factual information and technical details
- Speaker names (ALEX: and SARAH: only)
- The overall structure and episode flow
- Any specific numbers, dates, or statistics (but write them as words)

OUTPUT: Return the enhanced script only. No explanations or meta-commentary."""

# Perplexity research prompt for factual accuracy
PERPLEXITY_RESEARCH_PROMPT = """You are a research assistant helping create accurate podcast content.
Research the given topic and provide:
1. Current, accurate statistics and facts
2. Recent developments or news (within last 6 months if relevant)
3. Specific examples and case studies
4. Industry terminology explained clearly

Format your response as bullet points that can be easily incorporated into a podcast script.
Be concise but thorough. Cite sources when possible."""


class ThreadSafeRateLimiter:
    """Thread-safe adaptive rate limiter to prevent OpenAI 429 errors"""
    def __init__(self, max_requests=8, window_seconds=1):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests = deque()
        self._lock = RLock()  # Thread-safe lock for gevent

    def wait_if_needed(self):
        with self._lock:
            now = time.time()
            # Remove old requests outside window
            while self.requests and self.requests[0] < now - self.window:
                self.requests.popleft()

            if len(self.requests) >= self.max_requests:
                sleep_time = self.requests[0] + self.window - now
                if sleep_time > 0:
                    gevent.sleep(sleep_time)

            self.requests.append(time.time())


# Global rate limiter instances (thread-safe)
rate_limiter = ThreadSafeRateLimiter(max_requests=8, window_seconds=1)  # OpenAI TTS
perplexity_rate_limiter = ThreadSafeRateLimiter(max_requests=20, window_seconds=1)  # Perplexity research
claude_rate_limiter = ThreadSafeRateLimiter(max_requests=20, window_seconds=1)  # Claude enhancement


def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not APP_PASSWORD:
            return f(*args, **kwargs)
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def validate_file_upload(file):
    """
    Validate uploaded file for security.
    Returns (content, error_message) tuple.
    """
    if not file or not file.filename:
        return None, "No file provided"

    # Check file extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_FILE_EXTENSIONS:
        return None, f"Invalid file type. Allowed: {', '.join(ALLOWED_FILE_EXTENSIONS)}"

    # Check file size
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)

    if size > MAX_FILE_SIZE:
        return None, f"File too large. Maximum {MAX_FILE_SIZE / (1024*1024):.0f}MB allowed."

    # Try to decode as UTF-8
    try:
        content = file.read().decode('utf-8')
        return content, None
    except UnicodeDecodeError:
        return None, "Invalid file encoding. Please use UTF-8."


def sanitize_error_message(exc):
    """Convert exception to user-safe error message without exposing internals"""
    if isinstance(exc, openai.RateLimitError):
        return "Service busy. Please retry in a moment."
    elif isinstance(exc, openai.AuthenticationError):
        return "API authentication failed. Please contact support."
    elif isinstance(exc, openai.APIError):
        return "External service error. Please try again."
    elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "Request timeout. Try with shorter text."
    elif isinstance(exc, ValueError):
        return str(exc)  # ValueError messages are usually safe
    else:
        logger.exception("Unexpected error")
        return "An unexpected error occurred. Please try again."


def generate_job_id():
    """Generate collision-proof job ID"""
    return f"{uuid.uuid4().hex[:12]}_{int(time.time()*1000)}"


# Retry decorator for OpenAI API calls
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((openai.RateLimitError, openai.APITimeoutError, httpx.TimeoutException)),
    reraise=True
)
def call_openai_tts_with_retry(client, model, voice, text):
    """Call OpenAI TTS API with retry logic"""
    return client.audio.speech.create(
        model=model,
        voice=voice,
        input=text,
        response_format="mp3"
    )


# Singleton OpenAI client for connection pooling
_openai_client = None


def get_client():
    """Get OpenAI client with timeout (singleton for connection pooling)"""
    global _openai_client
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    if _openai_client is None:
        # Set 120 second timeout for TTS API calls
        _openai_client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=httpx.Timeout(120.0, connect=10.0)
        )
    return _openai_client


def preprocess_text(text):
    """
    Clean text for natural TTS reading.
    Handles technical content, code blocks, tables, and symbols.
    """
    # 1. Remove code blocks entirely (or describe them)
    text = re.sub(r'```[\s\S]*?```', ' [code example omitted] ', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)  # Remove backticks but keep content

    # 2. Remove box-drawing characters and ASCII art
    box_chars = '├└┌┐┬┴┼─│┘═║╔╗╚╝╠╣╦╩╬▼▲►◄●○■□'
    for char in box_chars:
        text = text.replace(char, ' ')

    # 3. Remove table structures
    text = re.sub(r'\|[-─=+]+\|', '', text)  # Table row separators
    text = re.sub(r'^\s*\|.*\|\s*$', '', text, flags=re.MULTILINE)  # Table rows

    # 4. Convert symbols to spoken equivalents
    symbol_map = {
        '→': ' leads to ',
        '←': ' from ',
        '↔': ' bidirectional ',
        '✅': 'Yes: ',
        '❌': 'No: ',
        '✓': 'check ',
        '✗': 'x ',
        '•': ', ',
        '…': '...',
        '::': ' ',
        '>=': ' greater than or equal to ',
        '<=': ' less than or equal to ',
        '!=': ' not equal to ',
        '==': ' equals ',
        '&&': ' and ',
        '||': ' or ',
        '>>': ' ',
        '<<': ' ',
        '**': '',
        '__': '',
        '//': ' ',
        '/*': ' ',
        '*/': ' ',
        '#{': ' ',
        '${': ' ',
        '@{': ' ',
    }
    for sym, spoken in symbol_map.items():
        text = text.replace(sym, spoken)

    # 5. Clean markdown links [text](url) → just text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

    # 6. Remove markdown headers but keep text
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)

    # 7. Remove emphasis markers
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # Bold
    text = re.sub(r'\*([^*]+)\*', r'\1', text)  # Italic
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)

    # 8. Remove horizontal rules and bullet points
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

    # 9. Clean special quote characters
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(''', "'").replace(''', "'")
    text = text.replace('—', ' - ')
    text = text.replace('–', ' - ')

    # 10. Remove file paths and URLs (they sound terrible in TTS)
    text = re.sub(r'https?://[^\s]+', '', text)
    text = re.sub(r'[A-Za-z]:\\[^\s]+', '', text)  # Windows paths
    text = re.sub(r'/[a-zA-Z0-9_/.-]+\.[a-z]+', '', text)  # Unix paths

    # 11. Clean up whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'  +', ' ', text)

    return text.strip()


def split_into_chunks(text, max_chars=2000):
    """Split text into chunks for OpenAI API (smaller chunks = faster processing)"""
    paragraphs = text.split('\n\n')
    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(para) > max_chars:
            sentences = re.split(r'(?<=[.!?])\s+', para)
            for sentence in sentences:
                if len(current) + len(sentence) < max_chars:
                    current += sentence + " "
                else:
                    if current:
                        chunks.append(current.strip())
                    current = sentence + " "
        elif len(current) + len(para) < max_chars:
            current += para + "\n\n"
        else:
            if current:
                chunks.append(current.strip())
            current = para + "\n\n"

    if current:
        chunks.append(current.strip())

    return chunks


def concatenate_mp3_files(file_paths, output_path):
    """Concatenate MP3 files using binary concatenation (works for MP3s)"""
    with open(output_path, 'wb') as outfile:
        for fpath in file_paths:
            with open(fpath, 'rb') as infile:
                outfile.write(infile.read())
    return output_path


# ============== Multiplier Logic: Dynamic Agent Scaling ==============

def estimate_audio_duration(text: str) -> dict:
    """
    Estimate audio duration from text content.

    TTS typically produces 150-180 words/minute for natural speech.
    We use 150 words/min as a conservative estimate.

    Returns dict with:
    - word_count: total words
    - char_count: total characters
    - estimated_minutes: estimated audio duration
    - recommended_agents: suggested agent count (1 per minute)
    """
    words = text.split()
    word_count = len(words)
    char_count = len(text)

    # Conservative estimate: 150 words/minute for TTS
    estimated_minutes = word_count / 150

    # Chunk estimation: ~2000 chars per TTS chunk
    estimated_chunks = max(1, char_count // 2000)

    # Agent recommendation: 1 per minute, bounded by MIN/MAX
    recommended_agents = max(MIN_AGENTS, min(MAX_AGENTS, int(estimated_minutes * AGENTS_PER_MINUTE)))

    return {
        'word_count': word_count,
        'char_count': char_count,
        'estimated_minutes': round(estimated_minutes, 1),
        'estimated_chunks': estimated_chunks,
        'recommended_agents': recommended_agents
    }


def calculate_scaled_agents(duration: dict, episode_count: int) -> dict:
    """
    Calculate optimal agent counts for each processing stage based on estimated duration.

    Implements "1 agent per minute of audio" scaling with configurable multiplier.

    Args:
        duration: Output from estimate_audio_duration()
        episode_count: Number of episodes to process

    Returns dict with:
        - perplexity_agents: Scaled Perplexity concurrent limit
        - claude_agents: Scaled Claude concurrent limit
        - tts_agents: Scaled TTS concurrent limit
        - use_batching: Whether to enable batch processing
        - batch_size: Episodes per batch (if batching enabled)
        - target_agents: Base target agent count
        - estimated_minutes: Estimated audio duration
    """
    if not MULTIPLIER_ENABLED:
        # Return current defaults when multiplier is disabled
        return {
            'perplexity_agents': MAX_PERPLEXITY_CONCURRENT,
            'claude_agents': MAX_CLAUDE_CONCURRENT,
            'tts_agents': MAX_CONCURRENT_CHUNKS if MAX_CONCURRENT_CHUNKS > 0 else 0,
            'use_batching': False,
            'batch_size': episode_count,
            'target_agents': MAX_PERPLEXITY_CONCURRENT,
            'estimated_minutes': duration.get('estimated_minutes', 0)
        }

    minutes = duration.get('estimated_minutes', 0)

    # Base calculation: 1 agent per minute of estimated audio
    target_agents = int(minutes * AGENTS_PER_MINUTE)
    target_agents = max(MIN_AGENTS, min(MAX_AGENTS, target_agents))

    # Determine if batching is needed for very long content
    use_batching = minutes >= BATCH_THRESHOLD_MINUTES or episode_count > MAX_EPISODES_PER_BATCH
    batch_size = MAX_EPISODES_PER_BATCH if use_batching else episode_count

    # Scale each provider independently
    # Perplexity: generally more forgiving, scale fully
    perplexity_agents = min(target_agents, MAX_AGENTS)

    # Claude: slightly more conservative due to token limits
    claude_agents = min(int(target_agents * 0.8), MAX_AGENTS)

    # TTS: OpenAI is rate-limited at 8/sec, cap to prevent excessive 429s
    tts_agents = min(target_agents, 50)

    logger.info(f"Multiplier scaling: {minutes:.1f} min -> {target_agents} agents "
                f"(Perplexity={perplexity_agents}, Claude={claude_agents}, TTS={tts_agents}), "
                f"batching={use_batching}")

    return {
        'perplexity_agents': perplexity_agents,
        'claude_agents': claude_agents,
        'tts_agents': tts_agents,
        'use_batching': use_batching,
        'batch_size': batch_size,
        'target_agents': target_agents,
        'estimated_minutes': minutes
    }


def process_episodes_in_batches(episodes: list, batch_size: int, process_func: callable,
                                 pool_size: int, stage_name: str = "processing") -> list:
    """
    Process episodes in batches to prevent memory exhaustion for long content.

    Args:
        episodes: List of episode dicts to process
        batch_size: Max episodes per batch
        process_func: Function to call for each episode
        pool_size: GeventPool size for parallel processing
        stage_name: Name for logging

    Returns:
        List of all results from all batches
    """
    total_episodes = len(episodes)
    num_batches = (total_episodes + batch_size - 1) // batch_size  # Ceiling division
    all_results = []

    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, total_episodes)
        batch = episodes[start_idx:end_idx]

        logger.info(f"Batch {batch_num + 1}/{num_batches}: {stage_name} episodes {start_idx + 1}-{end_idx} "
                    f"with {pool_size} parallel agents")

        # Process this batch in parallel
        pool = GeventPool(size=pool_size)
        batch_results = list(pool.imap_unordered(process_func, batch))
        all_results.extend(batch_results)

        # Cooldown between batches (except last batch)
        if batch_num < num_batches - 1 and BATCH_COOLDOWN_SECONDS > 0:
            logger.info(f"Batch cooldown: {BATCH_COOLDOWN_SECONDS}s before next batch")
            gevent.sleep(BATCH_COOLDOWN_SECONDS)

    return all_results


def detect_speakers(text):
    """Extract unique speaker names from script (e.g., ALEX:, SARAH:)"""
    # Match patterns like "ALEX:", "Sarah:", "GUEST EXPERT:" at start of lines
    pattern = r'^([A-Z][A-Za-z0-9 ]+?):\s'
    speakers = set(re.findall(pattern, text, re.MULTILINE))
    return list(speakers)


def detect_gender(name):
    """Detect gender from speaker name using common name lists"""
    # Extract first word of name (e.g., "Guest Expert" -> "guest")
    first_name = name.lower().split()[0]

    if first_name in MALE_NAMES:
        return 'male'
    elif first_name in FEMALE_NAMES:
        return 'female'
    return 'neutral'


def get_voice_for_speaker(speaker_name):
    """Get the appropriate voice for a speaker based on gender detection"""
    gender = detect_gender(speaker_name)
    return VOICE_MAP.get(gender, 'alloy')


def assign_speaker_voices(speakers):
    """
    Assign voices ensuring diversity for 2-speaker podcasts.
    For 2-host shows: ALWAYS uses echo (male) + shimmer (female) for clarity.
    """
    if len(speakers) == 0:
        return {}

    if len(speakers) == 1:
        return {speakers[0]: get_voice_for_speaker(speakers[0])}

    if len(speakers) == 2:
        # ALWAYS force male + female for 2-host podcasts - maximum clarity
        # First speaker gets echo (male deep voice)
        # Second speaker gets shimmer (female voice)
        voice_assignment = {
            speakers[0]: 'echo',     # Male voice
            speakers[1]: 'shimmer'   # Female voice
        }
        logger.info(f"2-host podcast: {speakers[0]}=echo(male), {speakers[1]}=shimmer(female)")
        return voice_assignment

    # 3+ speakers: use detected genders, but try to ensure variety
    voices = {}
    used_voices = set()
    for speaker in speakers:
        voice = get_voice_for_speaker(speaker)
        # Try to avoid duplicates for first 3 speakers
        if voice in used_voices and len(used_voices) < 3:
            for alt in ['echo', 'shimmer', 'alloy']:
                if alt not in used_voices:
                    voice = alt
                    break
        voices[speaker] = voice
        used_voices.add(voice)
    return voices


def generate_smart_filename(text):
    """
    Generate intelligent filename from script content.
    Extracts episode title, first speaker line, or first words.
    """
    # Try episode header first (EPISODE 1: Title, ## Episode 5: Title, etc.)
    episode_match = re.search(r'(?:##?\s*)?EPISODE\s*\d+[:\s]+(.+?)(?:\n|$)', text, re.IGNORECASE)
    if episode_match:
        title = episode_match.group(1).strip()
    else:
        # Try first speaker line
        speaker_match = re.search(r'^[A-Z][A-Za-z]+:\s*(.+?)(?:\.|$)', text, re.MULTILINE)
        if speaker_match:
            title = speaker_match.group(1)[:50]
        else:
            # Fallback: first N meaningful words
            words = re.findall(r'\b[a-zA-Z]{3,}\b', text[:300])
            title = ' '.join(words[:6]) if words else 'podcast'

    # Slugify: lowercase, replace non-alphanumeric with dash, trim
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:40]

    # Add date for uniqueness
    date = datetime.now().strftime('%Y%m%d')

    return f"{slug}-{date}.mp3"


def split_by_speaker(text, speaker_voices, max_chars=2000):
    """
    Split text into chunks by speaker, respecting max character limit.
    Returns list of dicts: {'speaker': 'ALEX', 'text': '...', 'voice': 'echo'}
    """
    # Pattern to match speaker lines like "ALEX:" or "SARAH:" at start
    speaker_pattern = r'^([A-Z][A-Za-z0-9 ]+?):\s*'

    segments = []
    current_speaker = None
    current_text = []

    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # Check if this line starts with a speaker name
        match = re.match(speaker_pattern, line)
        if match:
            # Save previous speaker's content
            if current_speaker and current_text:
                segments.append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text),
                    'voice': speaker_voices.get(current_speaker, 'alloy')
                })

            # Start new speaker
            current_speaker = match.group(1)
            # Get the text after the speaker name
            remaining_text = line[match.end():].strip()
            current_text = [remaining_text] if remaining_text else []
        elif current_speaker:
            # Continuation of current speaker's dialogue
            current_text.append(line)

    # Don't forget the last speaker's content
    if current_speaker and current_text:
        segments.append({
            'speaker': current_speaker,
            'text': ' '.join(current_text),
            'voice': speaker_voices.get(current_speaker, 'alloy')
        })

    # Now split any segments that exceed max_chars while preserving speaker/voice
    final_chunks = []
    for seg in segments:
        text = seg['text']
        if len(text) <= max_chars:
            if text.strip():  # Only add non-empty chunks
                final_chunks.append(seg)
        else:
            # Split long text into smaller chunks by sentences
            sentences = re.split(r'(?<=[.!?])\s+', text)
            current_chunk = ""

            for sentence in sentences:
                if len(current_chunk) + len(sentence) < max_chars:
                    current_chunk += sentence + " "
                else:
                    if current_chunk.strip():
                        final_chunks.append({
                            'speaker': seg['speaker'],
                            'text': current_chunk.strip(),
                            'voice': seg['voice']
                        })
                    current_chunk = sentence + " "

            # Add remaining text
            if current_chunk.strip():
                final_chunks.append({
                    'speaker': seg['speaker'],
                    'text': current_chunk.strip(),
                    'voice': seg['voice']
                })

    return final_chunks


# ============== Script Auto-Expansion Functions ==============

def is_episode_incomplete(episode_text):
    """
    Detect if an episode section is incomplete (outline/notes vs full dialogue).
    Returns True if the episode needs AI expansion.
    """
    # Check for dialogue markers (SPEAKER: format)
    has_dialogue = bool(re.search(r'^[A-Z][A-Za-z]+:', episode_text, re.MULTILINE))

    # Check for outline/instruction markers
    has_bullets = bool(re.search(r'^\s*[-*•]\s+', episode_text, re.MULTILINE))
    has_numbered_list = bool(re.search(r'^\s*\d+[.)]\s+', episode_text, re.MULTILINE))
    has_instructions = bool(re.search(
        r'\b(Cover|Explain|Describe|Include|Show|Discuss|Talk about|Mention|Address)\b',
        episode_text,
        re.IGNORECASE
    ))

    # Check for parenthetical instructions like "(describe the workflow)"
    has_paren_instructions = bool(re.search(r'\([^)]*\b(describe|explain|cover|discuss)\b[^)]*\)', episode_text, re.IGNORECASE))

    # Word count check (excluding header)
    lines = episode_text.strip().split('\n')
    content_lines = [l for l in lines[1:] if l.strip()]  # Skip header
    word_count = sum(len(line.split()) for line in content_lines)

    # Episode is incomplete if:
    # 1. No dialogue AND has outline markers (bullets, numbers, or paren instructions), OR
    # 2. Has parenthetical instructions like "(describe...)" even if it has some dialogue, OR
    # 3. Very short content with instruction words
    if not has_dialogue and (has_bullets or has_numbered_list):
        return True
    if has_paren_instructions:  # Always expand if has "(describe...)" style instructions
        return True
    if word_count < 200 and has_instructions:
        return True

    return False


def parse_episodes(text):
    """
    Split script into episodes and detect which ones are incomplete.
    Returns list of dicts with 'text', 'header', 'is_complete', 'episode_num'.
    """
    # Pattern to match episode headers like "## EPISODE 1:" or "EPISODE 1:"
    episode_pattern = r'((?:##?\s*)?EPISODE\s*(\d+)[:\s].+?)(?=(?:##?\s*)?EPISODE\s*\d+[:\s]|\Z)'

    matches = list(re.finditer(episode_pattern, text, re.DOTALL | re.IGNORECASE))

    if not matches:
        # No episode structure found - treat as single complete script
        return [{'text': text, 'header': '', 'is_complete': True, 'episode_num': 1}]

    episodes = []
    for match in matches:
        episode_text = match.group(1).strip()
        episode_num = int(match.group(2))

        # Extract header (first line)
        header_match = re.match(r'^[^\n]+', episode_text)
        header = header_match.group(0) if header_match else f"Episode {episode_num}"

        episodes.append({
            'text': episode_text,
            'header': header,
            'is_complete': not is_episode_incomplete(episode_text),
            'episode_num': episode_num
        })

    return episodes


def expand_script_with_ai(outline_text, context="", speakers=None, research_context=""):
    """
    Use GPT-4o to expand an episode outline into full podcast dialogue.
    Always uses ALEX (male host) and SARAH (female expert) for conversational format.

    Args:
        outline_text: The incomplete episode outline
        context: Text from previous complete episodes for style matching
        speakers: Ignored - always uses ALEX and SARAH for consistency
        research_context: Research findings from Perplexity to incorporate into dialogue

    Returns:
        Expanded dialogue script with ALEX and SARAH speakers
    """
    client = get_client()

    # Always force two-person format with ALEX and SARAH
    speakers = ['ALEX', 'SARAH']
    speaker_info = f"\nSpeakers: ALEX (male host/interviewer) and SARAH (female expert/analyst)"

    context_snippet = ""
    if context:
        # Limit context to last ~2000 chars to avoid token limits
        context_snippet = f"\n\nStyle reference from previous episodes:\n{context[-2000:]}"

    # Add research findings if available
    research_section = ""
    if research_context:
        research_section = f"""

RESEARCH FINDINGS TO INCORPORATE:
{research_context}

Use these facts, statistics, and insights naturally in the dialogue. Have SARAH cite specific data points and ALEX react with interest."""

    user_prompt = f"""Expand this episode outline into a full podcast dialogue script:{speaker_info}{context_snippet}{research_section}

EPISODE TO EXPAND:
{outline_text}

Write the complete dialogue now:"""

    try:
        response = client.chat.completions.create(
            model=SCRIPT_EXPANSION_MODEL,
            messages=[
                {"role": "system", "content": SCRIPT_EXPANSION_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=4000
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error expanding script: {e}")
        raise


def research_outline_with_perplexity(outline_text, episode_num=1):
    """
    Research an outline topic using Perplexity BEFORE expansion.
    Lighter-weight than full episode research - focused on getting key facts.

    Args:
        outline_text: The episode outline/topic to research
        episode_num: Episode number for tracking

    Returns:
        Dict with research content, citations, success status, and episode_num
    """
    if not PERPLEXITY_API_KEY:
        logger.debug(f"Episode {episode_num}: No Perplexity API key, skipping pre-expansion research")
        return {'research': '', 'citations': [], 'success': False, 'episode_num': episode_num}

    try:
        perplexity_rate_limiter.wait_if_needed()

        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "sonar-pro",
                "messages": [
                    {"role": "system", "content": "You are a research assistant for podcast content. Provide key facts, recent statistics, expert insights, and compelling information. Be concise but informative. Focus on surprising, specific, or noteworthy details that will make the podcast engaging."},
                    {"role": "user", "content": f"Research this podcast topic and provide 5-8 key facts, statistics, or insights:\n\n{outline_text[:1500]}"}
                ],
                "temperature": 0.3,
                "max_tokens": 800,
                "return_citations": True
            },
            timeout=60
        )

        if response.status_code == 200:
            response_json = response.json()
            research = response_json['choices'][0]['message']['content']
            citations = response_json.get('citations', [])
            logger.info(f"Episode {episode_num}: Pre-expansion research found {len(citations)} sources")
            return {'research': research, 'citations': citations, 'success': True, 'episode_num': episode_num}
        else:
            logger.warning(f"Episode {episode_num}: Perplexity returned {response.status_code}")
            return {'research': '', 'citations': [], 'success': False, 'episode_num': episode_num}
    except Exception as e:
        logger.error(f"Episode {episode_num}: Pre-expansion research failed: {e}")
        return {'research': '', 'citations': [], 'success': False, 'episode_num': episode_num}


def auto_expand_script(text, progress_callback=None):
    """
    Automatically detect and expand incomplete episodes in a script.
    Now runs Perplexity research FIRST to inform expansion with facts.

    Args:
        text: Full script text
        progress_callback: Optional function to call with progress updates

    Returns:
        Tuple of (expanded_text, expansion_count, all_citations)
    """
    episodes = parse_episodes(text)

    if not episodes or all(ep['is_complete'] for ep in episodes):
        return text, 0, []

    # Find incomplete episodes
    incomplete = [ep for ep in episodes if not ep['is_complete']]
    complete = [ep for ep in episodes if ep['is_complete']]

    if not incomplete:
        return text, 0, []

    # Extract speakers from complete episodes for consistency
    speakers = detect_speakers('\n'.join(ep['text'] for ep in complete))
    if not speakers:
        speakers = ['ALEX', 'SARAH']  # Default speakers

    # Build context from complete episodes
    context = '\n\n'.join(ep['text'] for ep in complete[:3])  # Use first 3 complete episodes

    # ===== RESEARCH PHASE: Get facts for each incomplete episode FIRST =====
    all_citations = []
    research_map = {}

    if PERPLEXITY_API_KEY:
        if progress_callback:
            progress_callback(f"Researching {len(incomplete)} episode(s) before expansion...")

        logger.info(f"Starting pre-expansion research for {len(incomplete)} episodes")

        # Research all incomplete episodes in parallel
        research_pool = GeventPool(size=min(5, len(incomplete)))
        research_results = list(research_pool.imap_unordered(
            lambda ep: research_outline_with_perplexity(ep['text'], ep['episode_num']),
            incomplete
        ))

        # Process research results
        for result in research_results:
            if result['success']:
                research_map[result['episode_num']] = result['research']
                all_citations.extend(result.get('citations', []))
                logger.info(f"Episode {result['episode_num']}: Research ready ({len(result['research'])} chars)")

        logger.info(f"Pre-expansion research complete: {len(research_map)} episodes researched, {len(all_citations)} citations")

    # ===== EXPANSION PHASE: Expand WITH research context =====
    expanded_episodes = {}
    for i, ep in enumerate(incomplete):
        if progress_callback:
            progress_callback(f"Expanding Episode {ep['episode_num']} with AI... ({i+1}/{len(incomplete)})")

        logger.info(f"Expanding Episode {ep['episode_num']}...")

        # Get research for this episode (if available)
        episode_research = research_map.get(ep['episode_num'], '')

        try:
            expanded_text = expand_script_with_ai(
                ep['text'],
                context,
                speakers,
                research_context=episode_research  # Pass research to expansion!
            )
            expanded_episodes[ep['episode_num']] = expanded_text
            research_note = f" (with {len(episode_research)} chars research)" if episode_research else ""
            logger.info(f"Episode {ep['episode_num']} expanded ({len(expanded_text)} chars){research_note}")
        except Exception as e:
            logger.error(f"Failed to expand Episode {ep['episode_num']}: {e}")
            # Keep original text on failure
            expanded_episodes[ep['episode_num']] = ep['text']

    # Rebuild the full script with expanded episodes (preserving headers)
    result_parts = []
    for ep in episodes:
        if ep['episode_num'] in expanded_episodes:
            # Preserve episode header before expanded content
            header = ep['header']
            expanded = expanded_episodes[ep['episode_num']]
            # Only add header if expanded text doesn't already start with it
            if not expanded.strip().upper().startswith(header.strip().upper()[:20]):
                result_parts.append(f"{header}\n\n{expanded}")
            else:
                result_parts.append(expanded)
        else:
            result_parts.append(ep['text'])

    return '\n\n'.join(result_parts), len(incomplete), all_citations


# ============== Multi-AI Enhancement Pipeline ==============

# Singleton Claude client for connection pooling
_claude_client = None


def get_claude_client():
    """Get Anthropic Claude client with timeout (singleton for connection pooling)"""
    global _claude_client
    if not CLAUDE_API_KEY:
        return None
    if _claude_client is None:
        _claude_client = anthropic.Anthropic(
            api_key=CLAUDE_API_KEY,
            timeout=httpx.Timeout(120.0, connect=10.0)
        )
    return _claude_client


def research_episode_with_perplexity(episode_data):
    """
    Research a single episode topic using Perplexity API.
    Called in parallel for each episode.
    Returns research content AND source citations.
    """
    episode_num = episode_data.get('episode_num', 0)
    episode_text = episode_data.get('text', '')
    header = episode_data.get('header', '')

    # Check if API key is configured
    if not PERPLEXITY_API_KEY:
        return {'episode_num': episode_num, 'research': '', 'citations': [], 'success': False}

    # Extract the main topic from the episode header/content
    topic = header if header else episode_text[:500]

    try:
        # Rate limit Perplexity API calls
        perplexity_rate_limiter.wait_if_needed()

        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "sonar-pro",
                "messages": [
                    {"role": "system", "content": PERPLEXITY_RESEARCH_PROMPT},
                    {"role": "user", "content": f"Research this podcast episode topic:\n\n{topic}\n\nProvide relevant facts, statistics, and current information."}
                ],
                "temperature": 0.3,
                "max_tokens": 1000,
                "return_citations": True  # Request citations from Perplexity
            },
            timeout=60
        )

        if response.status_code == 200:
            response_json = response.json()
            research_data = response_json['choices'][0]['message']['content']

            # Extract citations if available (Perplexity returns these in the response)
            citations = []
            if 'citations' in response_json:
                citations = response_json['citations']
            elif 'citations' in response_json.get('choices', [{}])[0].get('message', {}):
                citations = response_json['choices'][0]['message']['citations']

            # Also try to extract inline citation URLs from the content
            url_pattern = r'https?://[^\s\)\]>"]+'
            inline_urls = re.findall(url_pattern, research_data)
            for url in inline_urls:
                if url not in citations:
                    citations.append(url)

            logger.info(f"Episode {episode_num} researched: {len(citations)} sources found")
            return {
                'episode_num': episode_num,
                'research': research_data,
                'citations': citations[:10],  # Limit to top 10 sources
                'topic': topic[:100],  # Include topic for display
                'success': True
            }
        else:
            logger.warning(f"Episode {episode_num} research failed: {response.status_code}")
            return {'episode_num': episode_num, 'research': '', 'citations': [], 'success': False}

    except Exception as e:
        logger.error(f"Episode {episode_num} research error: {e}")
        return {'episode_num': episode_num, 'research': '', 'citations': [], 'success': False}


def compute_text_changes(original, enhanced):
    """
    Compute a summary of changes between original and enhanced text.
    Returns stats and sample changes for display.
    """
    original_words = set(original.lower().split())
    enhanced_words = set(enhanced.lower().split())

    added_words = enhanced_words - original_words
    removed_words = original_words - enhanced_words

    # Count lines changed
    original_lines = original.strip().split('\n')
    enhanced_lines = enhanced.strip().split('\n')

    # Simple diff: find lines that are new or significantly different
    new_lines = []
    for line in enhanced_lines:
        line_clean = line.strip()
        if line_clean and not any(line_clean in orig for orig in original_lines):
            # This is a new or modified line
            if len(line_clean) > 20:  # Only meaningful lines
                new_lines.append(line_clean[:100] + ('...' if len(line_clean) > 100 else ''))

    return {
        'words_added': len(added_words),
        'words_removed': len(removed_words),
        'lines_original': len(original_lines),
        'lines_enhanced': len(enhanced_lines),
        'sample_additions': new_lines[:5],  # First 5 new/changed lines
        'length_change': len(enhanced) - len(original)
    }


def enhance_episode_with_claude(episode_data):
    """
    Enhance a single episode using Claude for natural dialogue.
    Called in parallel for each episode.
    Returns enhanced text AND change tracking information.
    """
    episode_num = episode_data.get('episode_num', 0)
    episode_text = episode_data.get('text', '')
    speakers = episode_data.get('speakers', [])
    research_context = episode_data.get('research', '')  # Get research separately

    # Check if Claude is configured
    client = get_claude_client()
    if not client:
        logger.warning(f"Episode {episode_num}: Claude API not configured, skipping enhancement")
        return {
            'episode_num': episode_num,
            'enhanced_text': episode_text,  # Return original if no Claude
            'changes': None,
            'success': False,
            'error': 'Claude API not configured'
        }

    try:
        # Rate limit Claude API calls
        claude_rate_limiter.wait_if_needed()

        speaker_list = ', '.join(speakers) if speakers else 'ALEX, SARAH'

        # Build prompt with research context (but don't embed markers in output)
        prompt_parts = [CLAUDE_ENHANCEMENT_PROMPT, f"\n\nSPEAKERS: {speaker_list}"]

        if research_context:
            prompt_parts.append(f"\n\nRESEARCH NOTES (use to verify/enhance facts, do NOT include these markers in output):\n{research_context}")

        prompt_parts.append(f"\n\nEPISODE TO ENHANCE:\n{episode_text}")

        # Try primary model, fallback to older model if not available
        model_to_use = CLAUDE_MODEL
        try:
            response = client.messages.create(
                model=model_to_use,
                max_tokens=8000,
                messages=[
                    {
                        "role": "user",
                        "content": ''.join(prompt_parts)
                    }
                ]
            )
        except (anthropic.NotFoundError, anthropic.BadRequestError) as model_err:
            # Model not found or bad request - try fallback model with reduced tokens
            logger.warning(f"Episode {episode_num}: Model {model_to_use} failed ({type(model_err).__name__}), trying fallback {CLAUDE_MODEL_FALLBACK}")
            model_to_use = CLAUDE_MODEL_FALLBACK
            # Haiku has 4096 max output tokens, reduce accordingly
            fallback_max_tokens = 4096 if 'haiku' in CLAUDE_MODEL_FALLBACK.lower() else 8000
            response = client.messages.create(
                model=model_to_use,
                max_tokens=fallback_max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": ''.join(prompt_parts)
                    }
                ]
            )

        enhanced_text = response.content[0].text
        logger.info(f"Episode {episode_num} enhanced using model: {model_to_use}")

        # Strip any research markers that might have leaked through
        enhanced_text = re.sub(r'\[RESEARCH CONTEXT\].*?\[END RESEARCH\]', '', enhanced_text, flags=re.DOTALL)
        enhanced_text = re.sub(r'\[RESEARCH NOTES\].*?\[END NOTES\]', '', enhanced_text, flags=re.DOTALL)

        # Compute changes between original and enhanced
        changes = compute_text_changes(episode_text, enhanced_text)

        logger.info(f"Episode {episode_num} enhanced ({len(enhanced_text)} chars, +{changes['words_added']} words)")

        return {
            'episode_num': episode_num,
            'enhanced_text': enhanced_text,
            'changes': changes,
            'original_preview': episode_text[:200] + '...' if len(episode_text) > 200 else episode_text,
            'success': True
        }

    except Exception as e:
        error_str = str(e)
        logger.error(f"Episode {episode_num} enhancement error: {error_str}")
        return {
            'episode_num': episode_num,
            'enhanced_text': episode_text,  # Return original on failure
            'changes': None,
            'success': False,
            'error': f"Claude API error: {error_str[:100]}"  # Include actual error
        }


def run_ai_enhancement_pipeline(text, progress_callback=None):
    """
    Run the full multi-AI enhancement pipeline with parallel agents.

    Pipeline stages:
    1. Perplexity Research (parallel per episode, limited concurrency)
    2. GPT-4o Expansion (parallel per incomplete episode) - already called separately
    3. Claude Enhancement (parallel per episode, limited concurrency)

    Returns: (enhanced_text, stats_dict) where stats_dict includes:
    - research_count, enhance_count
    - all_citations: list of all sources found
    - all_changes: list of change summaries per episode
    - research_findings: research summaries per episode
    """
    episodes = parse_episodes(text)
    total_episodes = len(episodes)

    if total_episodes == 0:
        return text, {'research_count': 0, 'enhance_count': 0, 'all_citations': [], 'all_changes': [], 'research_findings': []}

    # Extract speakers for consistency
    speakers = detect_speakers(text)
    if not speakers:
        speakers = ['ALEX', 'SARAH']

    stats = {
        'research_count': 0,
        'enhance_count': 0,
        'all_citations': [],
        'all_changes': [],
        'research_findings': []
    }

    # Enterprise-grade parallel processing with separate limits per AI provider
    research_pool_size = min(MAX_PERPLEXITY_CONCURRENT, total_episodes)
    enhance_pool_size = min(MAX_CLAUDE_CONCURRENT, total_episodes)

    # Stage 1: Perplexity Research - PARALLEL (limited concurrency)
    if progress_callback:
        progress_callback(f"Researching {total_episodes} episodes with Perplexity ({research_pool_size} parallel agents)...")

    logger.info(f"Starting Perplexity research for {total_episodes} episodes ({research_pool_size} concurrent)")

    research_pool = GeventPool(size=research_pool_size)
    research_results = list(research_pool.imap_unordered(
        research_episode_with_perplexity,
        episodes
    ))

    # Build research context map and collect citations
    research_map = {}
    for result in research_results:
        if result['success']:
            research_map[result['episode_num']] = result['research']
            stats['research_count'] += 1

            # Collect citations from this episode
            for citation in result.get('citations', []):
                if citation not in stats['all_citations']:
                    stats['all_citations'].append(citation)

            # Collect research findings summary
            stats['research_findings'].append({
                'episode': result['episode_num'],
                'topic': result.get('topic', ''),
                'findings_preview': result['research'][:300] + '...' if len(result['research']) > 300 else result['research'],
                'source_count': len(result.get('citations', []))
            })

    logger.info(f"Research complete: {stats['research_count']}/{total_episodes} successful, {len(stats['all_citations'])} sources")

    # Stage 2: Claude Enhancement - PARALLEL (limited concurrency)
    if progress_callback:
        progress_callback(f"Enhancing {total_episodes} episodes with Claude ({enhance_pool_size} parallel agents)...")

    logger.info(f"Starting Claude enhancement for {total_episodes} episodes ({enhance_pool_size} concurrent)")

    # Prepare episode data with speakers and research (passed separately, not embedded)
    for ep in episodes:
        ep['speakers'] = speakers
        ep_num = ep['episode_num']
        ep['research'] = research_map.get(ep_num, '')  # Pass research separately

    enhance_pool = GeventPool(size=enhance_pool_size)
    enhance_results = list(enhance_pool.imap_unordered(
        enhance_episode_with_claude,
        episodes
    ))

    # Build enhanced text map and collect changes
    enhanced_map = {}
    for result in enhance_results:
        enhanced_map[result['episode_num']] = result['enhanced_text']
        if result['success']:
            stats['enhance_count'] += 1

            # Collect change information
            if result.get('changes'):
                stats['all_changes'].append({
                    'episode': result['episode_num'],
                    'words_added': result['changes']['words_added'],
                    'words_removed': result['changes']['words_removed'],
                    'length_change': result['changes']['length_change'],
                    'sample_additions': result['changes']['sample_additions'][:3]  # Top 3 additions
                })

    logger.info(f"Enhancement complete: {stats['enhance_count']}/{total_episodes} successful")

    # Rebuild full script with enhanced episodes (in order)
    result_parts = []
    for ep in sorted(episodes, key=lambda x: x['episode_num']):
        ep_num = ep['episode_num']
        if ep_num in enhanced_map:
            # Strip any remaining markers from the output (safety check)
            clean_text = re.sub(r'\[RESEARCH[^\]]*\].*?\[END[^\]]*\]', '', enhanced_map[ep_num], flags=re.DOTALL)
            result_parts.append(clean_text.strip())
        else:
            result_parts.append(ep['text'])

    enhanced_script = '\n\n'.join(result_parts)

    if progress_callback:
        progress_callback(f"AI enhancement complete! Researched {stats['research_count']}, enhanced {stats['enhance_count']} episodes.")

    return enhanced_script, stats


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")  # Rate limit login attempts
def login():
    """Login page with rate limiting"""
    if not APP_PASSWORD:
        return redirect(url_for('index'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == APP_PASSWORD:
            session['authenticated'] = True
            logger.info(f"Successful login from {get_remote_address()}")
            return redirect(url_for('index'))
        logger.warning(f"Failed login attempt from {get_remote_address()}")
        return render_template('login.html', error='Invalid password')

    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout"""
    session.pop('authenticated', None)
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """Main page"""
    return render_template('index.html', voices=VOICES, models=MODELS)


@app.route('/generate', methods=['POST'])
@login_required
@limiter.limit("10 per hour")  # Rate limit audio generation
def generate():
    """Generate audio from text - streams progress via SSE"""

    # Extract request data BEFORE creating generator (to avoid request context issues)
    if 'file' in request.files and request.files['file'].filename:
        file = request.files['file']
        text, error = validate_file_upload(file)
        if error:
            logger.warning(f"File upload validation failed: {error}")
            return jsonify({'status': 'error', 'message': error}), 400
    else:
        text = request.form.get('text', '')
        # Validate text length
        if len(text) > MAX_TEXT_LENGTH:
            logger.warning(f"Text too large: {len(text)} bytes (max {MAX_TEXT_LENGTH})")
            return jsonify({'status': 'error', 'message': f'Text too large. Maximum {MAX_TEXT_LENGTH//(1024*1024)}MB allowed.'}), 400

    voice = request.form.get('voice', 'nova')
    model = request.form.get('model', 'tts-1-hd')
    multi_voice = request.form.get('multi_voice', 'false').lower() == 'true'
    auto_expand = request.form.get('auto_expand', 'true').lower() == 'true'  # Default ON
    ai_enhance = request.form.get('ai_enhance', 'true').lower() == 'true'  # Default ON

    # Auto-enable multi-voice for two-person conversational podcasts when auto-expand is on
    if auto_expand and not multi_voice:
        multi_voice = True
        logger.info("Auto-enabling multi-voice for two-person podcast format")

    def generate_stream(text, voice, model, multi_voice, auto_expand, ai_enhance):
        job_id = generate_job_id()  # Collision-proof job ID
        job_dir = TEMP_DIR / job_id
        job_dir.mkdir(exist_ok=True)
        logger.info(f"Starting job {job_id}")

        try:
            if not text.strip():
                yield f"data: {json.dumps({'error': 'No text provided'})}\n\n"
                return

            if voice not in VOICES:
                voice = 'nova'
            if model not in MODELS:
                model = 'tts-1-hd'

            # Track expansion state for skipping duplicate research
            expansion_citations = []
            expanded_count = 0

            # Auto-expand incomplete episodes if enabled
            if auto_expand:
                yield f"data: {{\"status\": \"processing\", \"stage\": \"analyze\", \"message\": \"Analyzing script for incomplete sections...\"}}\n\n"

                # Check for incomplete episodes
                episodes = parse_episodes(text)
                incomplete_count = sum(1 for ep in episodes if not ep['is_complete'])

                if incomplete_count > 0:
                    yield f"data: {{\"status\": \"processing\", \"stage\": \"research\", \"message\": \"Researching {incomplete_count} episode(s) before expansion...\"}}\n\n"
                    logger.info(f"Job {job_id}: Found {incomplete_count} incomplete episodes, researching then expanding...")

                    try:
                        # Research FIRST, then expand with research context (new pipeline order!)
                        text, expanded_count, expansion_citations = auto_expand_script(text)

                        # Show research + expansion results
                        research_msg = f" with {len(expansion_citations)} sources" if expansion_citations else ""
                        yield f"data: {{\"status\": \"processing\", \"stage\": \"analyze\", \"message\": \"Expanded {expanded_count} episode(s){research_msg}. Continuing...\"}}\n\n"
                        logger.info(f"Job {job_id}: Expansion complete, {expanded_count} episodes expanded, {len(expansion_citations)} citations from pre-research")
                    except Exception as e:
                        yield f"data: {{\"status\": \"processing\", \"stage\": \"analyze\", \"message\": \"Script expansion failed: {str(e)}. Continuing with original text...\"}}\n\n"
                        logger.error(f"Job {job_id}: Expansion failed: {e}")

            # Run AI Enhancement Pipeline (Perplexity Research + Claude Polish) - INLINE for real-time SSE
            if ai_enhance:
                try:
                    # Parse episodes
                    episodes = parse_episodes(text)
                    total_episodes = len(episodes)

                    if total_episodes == 0:
                        yield f"data: {{\"status\": \"processing\", \"stage\": \"research\", \"message\": \"No episodes detected, skipping AI enhancement\"}}\n\n"
                    else:
                        # ===== MULTIPLIER LOGIC: Calculate dynamic scaling =====
                        duration_estimate = estimate_audio_duration(text)
                        scaling = calculate_scaled_agents(duration_estimate, total_episodes)

                        # Send scaling info to UI
                        scaling_info = {
                            'status': 'processing',
                            'stage': 'analyze',
                            'message': f"Detected {duration_estimate['estimated_minutes']:.0f} min content ({duration_estimate['word_count']} words) - scaling to {scaling['target_agents']} parallel agents",
                            'scaling': {
                                'estimated_minutes': duration_estimate['estimated_minutes'],
                                'word_count': duration_estimate['word_count'],
                                'target_agents': scaling['target_agents'],
                                'use_batching': scaling['use_batching'],
                                'batch_size': scaling['batch_size']
                            }
                        }
                        yield f"data: {json.dumps(scaling_info)}\n\n"
                        logger.info(f"Job {job_id}: Multiplier logic - {duration_estimate['estimated_minutes']:.1f} min, {scaling['target_agents']} agents, batching={scaling['use_batching']}")

                        # Extract speakers for consistency
                        speakers = detect_speakers(text)
                        if not speakers:
                            speakers = ['ALEX', 'SARAH']

                        # Stats tracking
                        all_citations = []
                        all_changes = []
                        research_findings = []
                        research_map = {}

                        # ===== STAGE 1: PERPLEXITY RESEARCH (PARALLEL with scaled agents) =====
                        # Skip if expansion already did research (facts are already in the dialogue!)
                        if expanded_count > 0 and expansion_citations:
                            # Research was already done during expansion - skip duplicate research
                            all_citations = expansion_citations
                            yield f"data: {{\"status\": \"processing\", \"stage\": \"research\", \"message\": \"Research completed during expansion ({len(expansion_citations)} sources). Proceeding to Claude enhancement...\"}}\n\n"
                            logger.info(f"Job {job_id}: Skipping Perplexity research (already done during expansion with {len(expansion_citations)} citations)")
                        else:
                            # Run full Perplexity research (content wasn't expanded, or no research during expansion)
                            research_pool_size = min(scaling['perplexity_agents'], total_episodes)
                            research_start = time.time()
                            yield f"data: {{\"status\": \"processing\", \"stage\": \"research\", \"message\": \"Starting Perplexity research with {research_pool_size} parallel agents...\"}}\n\n"
                            logger.info(f"Job {job_id}: Starting Perplexity research for {total_episodes} episodes ({research_pool_size} parallel)")

                            # Run all research requests in parallel
                            research_pool = GeventPool(size=research_pool_size)
                            research_results = list(research_pool.imap_unordered(
                                research_episode_with_perplexity,
                                episodes
                            ))

                            # Process results and send updates
                            completed = 0
                            for result in research_results:
                                completed += 1
                                ep_num = result.get('episode_num', 0)

                                if result['success']:
                                    research_map[ep_num] = result['research']

                                    # Collect citations (filter out generic AI sites)
                                    for citation in result.get('citations', []):
                                        # Skip generic AI company sites
                                        skip_domains = ['anthropic.com', 'claude.ai', 'openai.com', 'perplexity.ai', 'google.com', 'bing.com']
                                        if not any(domain in citation.lower() for domain in skip_domains):
                                            if citation not in all_citations:
                                                all_citations.append(citation)

                                    # Collect findings
                                    topic = result.get('topic', f'Episode {ep_num}')
                                    research_findings.append({
                                        'episode': ep_num,
                                        'topic': topic,
                                        'findings_preview': result['research'][:300] + '...' if len(result['research']) > 300 else result['research'],
                                        'source_count': len(result.get('citations', []))
                                    })

                                    # Send update with sources found
                                    yield f"data: {{\"status\": \"processing\", \"stage\": \"research\", \"message\": \"Episode {ep_num}: Found {len(result.get('citations', []))} sources\", \"current\": {completed}, \"total\": {total_episodes}}}\n\n"

                            # Send research complete with all citations and timing
                            research_time = time.time() - research_start
                            if all_citations:
                                research_data = {
                                    'status': 'processing',
                                    'stage': 'research',
                                    'message': f"Research complete in {research_time:.1f}s: {len(all_citations)} quality sources found ({total_episodes/max(research_time, 0.1):.1f} eps/sec)",
                                    'citations': all_citations[:15],
                                    'research_findings': research_findings
                                }
                                yield f"data: {json.dumps(research_data)}\n\n"

                        # ===== STAGE 2: CLAUDE ENHANCEMENT (PARALLEL with scaled agents) =====
                        enhance_pool_size = min(scaling['claude_agents'], total_episodes)
                        enhance_start = time.time()
                        yield f"data: {{\"status\": \"processing\", \"stage\": \"enhance\", \"message\": \"Starting Claude enhancement with {enhance_pool_size} parallel agents...\"}}\n\n"
                        logger.info(f"Job {job_id}: Starting Claude enhancement for {total_episodes} episodes ({enhance_pool_size} parallel)")

                        # Prepare all episodes with speakers and research context
                        for ep in episodes:
                            ep_num = ep.get('episode_num', 0)
                            ep['speakers'] = speakers
                            ep['research'] = research_map.get(ep_num, '')

                        # Run all enhancement requests in parallel
                        enhance_pool = GeventPool(size=enhance_pool_size)
                        enhance_results = list(enhance_pool.imap_unordered(
                            enhance_episode_with_claude,
                            episodes
                        ))

                        # Process results and send updates
                        enhanced_map = {}
                        completed = 0
                        for result in enhance_results:
                            completed += 1
                            ep_num = result.get('episode_num', 0)
                            enhanced_map[ep_num] = result['enhanced_text']
                            logger.info(f"Job {job_id}: Episode {ep_num} enhance result - success={result['success']}, has_changes={result.get('changes') is not None}, error={result.get('error')}")

                            if result['success'] and result.get('changes'):
                                changes = result['changes']
                                all_changes.append({
                                    'episode': ep_num,
                                    'words_added': changes['words_added'],
                                    'words_removed': changes['words_removed'],
                                    'length_change': changes['length_change'],
                                    'sample_additions': changes['sample_additions'][:3]
                                })

                                # Send update with changes
                                yield f"data: {{\"status\": \"processing\", \"stage\": \"enhance\", \"message\": \"Episode {ep_num}: +{changes['words_added']} words added\", \"current\": {completed}, \"total\": {total_episodes}}}\n\n"
                            elif result.get('error'):
                                # Claude not configured or error occurred - show specific error in UI
                                error_msg = result.get('error', 'Unknown error')
                                yield f"data: {{\"status\": \"processing\", \"stage\": \"enhance\", \"message\": \"Episode {ep_num}: ⚠️ {error_msg}\", \"current\": {completed}, \"total\": {total_episodes}}}\n\n"
                                logger.warning(f"Job {job_id}: Episode {ep_num} Claude error: {error_msg}")
                            else:
                                # Claude worked but no changes detected (shouldn't happen, but log it)
                                yield f"data: {{\"status\": \"processing\", \"stage\": \"enhance\", \"message\": \"Episode {ep_num}: No changes detected\", \"current\": {completed}, \"total\": {total_episodes}}}\n\n"
                                logger.warning(f"Job {job_id}: Episode {ep_num} - Claude returned success but no changes")

                        # Send enhancement complete with all changes and timing
                        enhance_time = time.time() - enhance_start
                        total_words_added = sum(c['words_added'] for c in all_changes) if all_changes else 0
                        enhance_data = {
                            'status': 'processing',
                            'stage': 'enhance',
                            'message': f"Enhancement complete in {enhance_time:.1f}s: +{total_words_added} words across {len(all_changes)} episodes ({total_episodes/max(enhance_time, 0.1):.1f} eps/sec)",
                            'changes': all_changes,
                            'total_citations': len(all_citations)
                        }
                        logger.info(f"Job {job_id}: Sending changes data: {len(all_changes)} changes")
                        yield f"data: {json.dumps(enhance_data)}\n\n"

                        # Rebuild full script with enhanced episodes
                        result_parts = []
                        for ep in sorted(episodes, key=lambda x: x.get('episode_num', 0)):
                            ep_num = ep.get('episode_num', 0)
                            if ep_num in enhanced_map:
                                clean_text = re.sub(r'\[RESEARCH[^\]]*\].*?\[END[^\]]*\]', '', enhanced_map[ep_num], flags=re.DOTALL)
                                result_parts.append(clean_text.strip())
                            else:
                                result_parts.append(ep['text'])

                        text = '\n\n'.join(result_parts)
                        logger.info(f"Job {job_id}: AI pipeline complete: {len(research_findings)} researched, {len(all_changes)} enhanced, {len(all_citations)} sources")

                except Exception as e:
                    yield f"data: {{\"status\": \"processing\", \"stage\": \"enhance\", \"message\": \"AI enhancement failed: {str(e)}. Continuing without enhancement...\"}}\n\n"
                    logger.error(f"Job {job_id}: Enhancement failed: {e}")

            # Preprocess text
            processed = preprocess_text(text)

            # Save transcript file for download
            transcript_path = job_dir / 'transcript.txt'
            try:
                with open(transcript_path, 'w', encoding='utf-8') as f:
                    f.write(f"# Podcast Transcript\n")
                    f.write(f"# Generated: {datetime.now().isoformat()}\n")
                    f.write(f"# Hosts: ALEX (male host) and SARAH (female expert)\n")
                    f.write(f"# Job ID: {job_id}\n\n")
                    f.write(text)  # Save the enhanced/expanded script
                logger.info(f"Job {job_id}: Transcript saved ({len(text)} chars)")
            except Exception as e:
                logger.warning(f"Job {job_id}: Failed to save transcript: {e}")

            # Check for multi-voice mode with speaker detection
            speaker_voices = {}
            if multi_voice:
                # Detect speakers from the original text (before preprocessing removes the markers)
                speakers = detect_speakers(text)
                if speakers:
                    # Use assign_speaker_voices for voice diversity (ensures 2-host shows have male+female)
                    speaker_voices = assign_speaker_voices(speakers)
                    logger.info(f"Job {job_id}: Multi-voice mode: detected {len(speakers)} speakers: {speaker_voices}")

            # Split text into chunks
            if speaker_voices:
                # Multi-voice: split by speaker boundaries
                chunks = split_by_speaker(text, speaker_voices)
                mode_desc = f"multi-voice ({len(speaker_voices)} speakers)"
            else:
                # Single voice: split by length only
                chunks = [{'text': chunk, 'voice': voice, 'speaker': None} for chunk in split_into_chunks(processed)]
                mode_desc = f"single-voice ({voice})"

            total_chunks = len(chunks)

            # Validate chunk count
            if total_chunks > MAX_CHUNKS:
                logger.warning(f"Job {job_id}: Too many chunks ({total_chunks} > {MAX_CHUNKS})")
                yield f"data: {{\"status\": \"error\", \"message\": \"Text too long. Maximum {MAX_CHUNKS} chunks allowed.\"}}\n\n"
                return

            yield f"data: {{\"status\": \"processing\", \"stage\": \"generate\", \"message\": \"Starting audio generation...\", \"total\": {total_chunks}}}\n\n"
            logger.info(f"Job {job_id}: Starting TTS generation: {total_chunks} chunks, model={model}, mode={mode_desc}")

            # Get OpenAI client
            client = get_client()

            # Generate audio for each chunk IN PARALLEL using gevent
            def generate_single_chunk(args):
                """Generate a single chunk - called by gevent greenlets"""
                idx, chunk_text, chunk_voice, chunk_speaker = args
                chunk_path = job_dir / f"chunk-{idx:03d}.mp3"

                try:
                    # Rate limit to prevent 429 errors
                    rate_limiter.wait_if_needed()

                    response = client.audio.speech.create(
                        model=model,
                        voice=chunk_voice,  # Use per-chunk voice
                        input=chunk_text,
                        response_format="mp3"
                    )
                    response.stream_to_file(str(chunk_path))

                    if chunk_path.exists() and chunk_path.stat().st_size > 0:
                        return (idx, chunk_path, chunk_voice, chunk_speaker, None)
                    else:
                        return (idx, None, chunk_voice, chunk_speaker, "Empty file generated")
                except Exception as e:
                    return (idx, None, chunk_voice, chunk_speaker, str(e))

            # Prepare chunk args (index, text, voice, speaker) for parallel processing
            chunk_args = [(i, chunk['text'], chunk['voice'], chunk.get('speaker')) for i, chunk in enumerate(chunks)]

            # Track results by index for ordered concatenation
            results = {}
            completed = 0
            errors = []

            # Use gevent pool for parallel API calls - scaled based on content length
            # Apply multiplier logic for TTS scaling
            tts_duration = estimate_audio_duration(text)
            tts_scaling = calculate_scaled_agents(tts_duration, 1)

            if MAX_CONCURRENT_CHUNKS > 0:
                pool_size = MAX_CONCURRENT_CHUNKS  # Explicit override
            elif tts_scaling['tts_agents'] > 0:
                pool_size = min(tts_scaling['tts_agents'], total_chunks)
            else:
                pool_size = total_chunks

            pool = GeventPool(size=pool_size)
            logger.info(f"Job {job_id}: TTS scaling - {tts_duration['estimated_minutes']:.1f} min -> {pool_size} concurrent agents for {total_chunks} chunks")

            # Process chunks in parallel
            for result in pool.imap_unordered(generate_single_chunk, chunk_args):
                idx, path, chunk_voice, chunk_speaker, error = result
                completed += 1

                if error:
                    logger.error(f"Job {job_id}: Chunk {idx+1} failed: {error}")
                    yield f"data: {{\"status\": \"error\", \"message\": \"Chunk {idx+1} failed: {sanitize_error_message(Exception(error))}\"}}\n\n"
                    return

                results[idx] = path
                speaker_info = f" [{chunk_speaker}:{chunk_voice}]" if chunk_speaker else f" [{chunk_voice}]"
                logger.debug(f"Job {job_id}: Chunk {idx+1} done{speaker_info} ({path.stat().st_size} bytes) [{completed}/{total_chunks}]")
                yield f"data: {{\"status\": \"processing\", \"stage\": \"generate\", \"message\": \"Generating audio... {completed}/{total_chunks}\", \"current\": {completed}, \"total\": {total_chunks}}}\n\n"

            # Get chunk files in correct order for concatenation
            chunk_files = [results[i] for i in sorted(results.keys())]

            # Concatenate chunks
            yield f"data: {{\"status\": \"processing\", \"stage\": \"combine\", \"message\": \"Combining audio chunks...\", \"current\": {total_chunks}, \"total\": {total_chunks}}}\n\n"

            output_path = job_dir / "podcast.mp3"
            concatenate_mp3_files(chunk_files, output_path)

            if output_path.exists():
                size_mb = output_path.stat().st_size / (1024 * 1024)
                # Generate smart filename based on content
                smart_filename = generate_smart_filename(text)
                yield f"data: {{\"status\": \"complete\", \"stage\": \"combine\", \"message\": \"Audio generated successfully!\", \"download_id\": \"{job_id}\", \"size_mb\": {size_mb:.1f}, \"filename\": \"{smart_filename}\", \"transcript_id\": \"{job_id}\"}}\n\n"
            else:
                yield f"data: {{\"status\": \"error\", \"message\": \"Failed to create final audio file\"}}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': f'Error: {str(e)}'})}\n\n"

    response = Response(generate_stream(text, voice, model, multi_voice, auto_expand, ai_enhance), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Connection'] = 'keep-alive'
    response.headers['X-Accel-Buffering'] = 'no'  # Critical for Render's nginx proxy
    return response


@app.route('/download/<job_id>')
@login_required
def download(job_id):
    """Download generated audio file"""
    # Sanitize job_id to prevent path traversal (allow alphanumeric, dash, underscore)
    job_id = re.sub(r'[^a-zA-Z0-9_-]', '', job_id)

    output_path = TEMP_DIR / job_id / "podcast.mp3"

    if not output_path.exists():
        return jsonify({'error': 'File not found'}), 404

    return send_file(
        output_path,
        as_attachment=True,
        download_name=f"podcast-{job_id}.mp3",
        mimetype='audio/mpeg'
    )


@app.route('/download-transcript/<job_id>')
@login_required
def download_transcript(job_id):
    """Download transcript file"""
    # Sanitize job_id to prevent path traversal
    job_id = re.sub(r'[^a-zA-Z0-9_-]', '', job_id)

    transcript_path = TEMP_DIR / job_id / "transcript.txt"

    if not transcript_path.exists():
        return jsonify({'error': 'Transcript not found'}), 404

    return send_file(
        transcript_path,
        as_attachment=True,
        download_name=f"podcast_transcript_{job_id[:8]}.txt",
        mimetype='text/plain'
    )


@app.route('/cleanup/<job_id>', methods=['POST'])
def cleanup(job_id):
    """Clean up temporary files after download"""
    job_id = re.sub(r'[^a-zA-Z0-9_-]', '', job_id)
    job_dir = TEMP_DIR / job_id

    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)

    return jsonify({'status': 'cleaned'})


@app.route('/health')
def health():
    """Comprehensive health check endpoint for Render"""
    checks = {
        'temp_directory': 'healthy' if TEMP_DIR.exists() and TEMP_DIR.is_dir() else 'unhealthy',
        'openai': 'configured' if OPENAI_API_KEY else 'not_configured',
        'claude': 'configured' if CLAUDE_API_KEY else 'not_configured',
        'perplexity': 'configured' if PERPLEXITY_API_KEY else 'not_configured'
    }

    # Overall health: temp_dir must work, OpenAI must be configured for core function
    core_healthy = checks['temp_directory'] == 'healthy' and checks['openai'] == 'configured'
    status = 'healthy' if core_healthy else 'degraded'

    return jsonify({
        'status': status,
        'checks': checks,
        'timestamp': datetime.utcnow().isoformat()
    }), 200 if core_healthy else 503


# ============== Interactive Pipeline API ==============
# Allows users to pause between stages and provide suggestions

MIN_RESEARCH_AGENTS = 5  # Always spawn at least 5 parallel Perplexity agents


def research_episode_parallel(episode_data: dict, user_suggestion: str = "") -> dict:
    """
    Research a single episode with MULTIPLE parallel Perplexity agents.
    Each agent researches a different angle for comprehensive coverage.

    Args:
        episode_data: Episode dict with 'text', 'header', 'episode_num'
        user_suggestion: Optional user guidance to focus research

    Returns:
        Dict with combined research, citations, agent count
    """
    episode_num = episode_data.get('episode_num', 0)
    episode_text = episode_data.get('text', '')
    header = episode_data.get('header', '')
    topic = header if header else episode_text[:500]

    if not PERPLEXITY_API_KEY:
        logger.debug(f"Episode {episode_num}: No Perplexity API key")
        return {'episode_num': episode_num, 'research': '', 'citations': [], 'success': False, 'agent_count': 0}

    # Generate multiple research angles
    research_angles = [
        f"Key statistics and quantitative data about: {topic}",
        f"Recent news and developments (2024-2025) about: {topic}",
        f"Expert opinions and analysis on: {topic}",
        f"Real-world examples and case studies of: {topic}",
        f"Common misconceptions or surprising facts about: {topic}",
    ]

    # Add user suggestion as additional focus if provided
    if user_suggestion:
        research_angles.append(f"{user_suggestion} regarding: {topic}")
        logger.info(f"Episode {episode_num}: Added user focus - {user_suggestion[:50]}...")

    def query_single_angle(angle: str) -> dict:
        """Query Perplexity for a single research angle"""
        try:
            perplexity_rate_limiter.wait_if_needed()
            response = requests.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "sonar-pro",
                    "messages": [
                        {"role": "system", "content": "You are a research assistant for podcast content. Provide concise, factual information with specific data points, statistics, and expert insights. Be informative but brief."},
                        {"role": "user", "content": angle}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 600,
                    "return_citations": True
                },
                timeout=60
            )
            if response.status_code == 200:
                data = response.json()
                return {
                    'content': data['choices'][0]['message']['content'],
                    'citations': data.get('citations', []),
                    'success': True
                }
        except Exception as e:
            logger.error(f"Research angle failed: {e}")
        return {'content': '', 'citations': [], 'success': False}

    # Run ALL angles in parallel
    pool = GeventPool(size=len(research_angles))
    results = list(pool.imap_unordered(query_single_angle, research_angles))

    # Combine results
    combined_research = []
    all_citations = []
    successful = 0

    for result in results:
        if result['success']:
            combined_research.append(result['content'])
            all_citations.extend(result['citations'])
            successful += 1

    # Deduplicate citations
    unique_citations = list(dict.fromkeys(all_citations))

    logger.info(f"Episode {episode_num}: {successful}/{len(research_angles)} research agents succeeded, {len(unique_citations)} unique citations")

    return {
        'episode_num': episode_num,
        'research': '\n\n---\n\n'.join(combined_research),
        'citations': unique_citations,
        'success': successful > 0,
        'agent_count': len(research_angles)
    }


def run_stage_analyze(job: Job) -> StageResult:
    """Analyze stage - detect episodes, check completeness"""
    text = job.text
    suggestion = job.user_suggestions.get(Stage.ANALYZE, '')

    # Parse episodes
    episodes = parse_episodes(text)
    job.episodes = episodes

    incomplete_count = sum(1 for ep in episodes if not ep['is_complete'])
    complete_count = len(episodes) - incomplete_count

    # Build preview for user review
    preview_lines = [
        f"Detected {len(episodes)} episode(s):",
        f"  - {complete_count} complete (ready for processing)",
        f"  - {incomplete_count} incomplete (will be expanded)",
        "",
        "Episodes found:"
    ]

    for ep in episodes[:5]:  # Show first 5
        status = "Complete" if ep['is_complete'] else "Needs expansion"
        header = ep.get('header', f"Episode {ep['episode_num']}")[:60]
        preview_lines.append(f"  {ep['episode_num']}. {header}... [{status}]")

    if len(episodes) > 5:
        preview_lines.append(f"  ... and {len(episodes) - 5} more")

    return StageResult(
        stage=Stage.ANALYZE,
        output_preview='\n'.join(preview_lines),
        full_output={'episodes': episodes, 'text': text},
        metadata={
            'episode_count': len(episodes),
            'incomplete_count': incomplete_count,
            'complete_count': complete_count
        }
    )


def run_stage_research(job: Job) -> StageResult:
    """Research stage - parallel multi-agent Perplexity research"""
    episodes = job.episodes
    suggestion = job.user_suggestions.get(Stage.RESEARCH, '')

    if not episodes:
        return StageResult(
            stage=Stage.RESEARCH,
            output_preview="No episodes to research.",
            full_output={'research_map': {}},
            metadata={'agent_count': 0}
        )

    # Research all episodes with multiple parallel agents EACH
    all_citations = []
    research_map = {}
    total_agents = 0

    preview_lines = [f"Research complete using {MIN_RESEARCH_AGENTS}+ parallel agents per episode:\n"]

    # Research each episode (in parallel across episodes too)
    episode_pool = GeventPool(size=min(5, len(episodes)))
    research_results = list(episode_pool.imap_unordered(
        lambda ep: research_episode_parallel(ep, suggestion),
        episodes
    ))

    for result in research_results:
        ep_num = result['episode_num']
        if result['success']:
            research_map[ep_num] = result['research']
            all_citations.extend(result['citations'])
            total_agents += result['agent_count']

            preview_lines.append(f"Episode {ep_num}: {len(result['citations'])} sources found")
            # Show snippet of findings
            snippet = result['research'][:200].replace('\n', ' ')
            preview_lines.append(f"  Key finding: {snippet}...\n")

    # Store in job
    job.research_map = research_map

    # Deduplicate citations
    unique_citations = list(dict.fromkeys(all_citations))

    preview_lines.insert(1, f"Total: {len(unique_citations)} unique sources across {total_agents} research queries\n")

    return StageResult(
        stage=Stage.RESEARCH,
        output_preview='\n'.join(preview_lines),
        full_output={'research_map': research_map},
        citations=unique_citations[:20],  # Top 20 for preview
        metadata={
            'total_agents': total_agents,
            'episodes_researched': len(research_map),
            'total_citations': len(unique_citations)
        }
    )


def run_stage_expand(job: Job) -> StageResult:
    """Expansion stage - GPT-4o expands outlines with research context"""
    episodes = job.episodes
    research_map = job.research_map
    suggestion = job.user_suggestions.get(Stage.EXPAND, '')

    incomplete = [ep for ep in episodes if not ep['is_complete']]

    if not incomplete:
        # All complete, skip expansion
        final_text = '\n\n'.join(ep['text'] for ep in episodes)
        job.final_text = final_text
        return StageResult(
            stage=Stage.EXPAND,
            output_preview="All episodes already complete. No expansion needed.",
            full_output={'expanded_text': final_text},
            metadata={'expanded_count': 0}
        )

    expanded_map = {}
    preview_lines = [f"Expanding {len(incomplete)} incomplete episode(s):\n"]

    for ep in incomplete:
        ep_num = ep['episode_num']
        research_context = research_map.get(ep_num, '')

        # Add user suggestion to expansion context
        style_guidance = ""
        if suggestion:
            style_guidance = f"\n\nUSER GUIDANCE: {suggestion}"

        try:
            expanded = expand_script_with_ai(
                ep['text'],
                context="",
                speakers=['ALEX', 'SARAH'],
                research_context=research_context + style_guidance
            )
            expanded_map[ep_num] = expanded
            preview_lines.append(f"Episode {ep_num}: Expanded to {len(expanded)} chars")
            # Show dialogue preview
            lines = expanded.split('\n')[:3]
            for line in lines:
                if line.strip():
                    preview_lines.append(f"  {line[:80]}...")
            preview_lines.append("")
        except Exception as e:
            logger.error(f"Expansion failed for episode {ep_num}: {e}")
            expanded_map[ep_num] = ep['text']
            preview_lines.append(f"Episode {ep_num}: Expansion failed, using original")

    # Rebuild full text
    result_parts = []
    for ep in episodes:
        if ep['episode_num'] in expanded_map:
            header = ep['header']
            expanded = expanded_map[ep['episode_num']]
            if not expanded.strip().upper().startswith(header.strip().upper()[:20]):
                result_parts.append(f"{header}\n\n{expanded}")
            else:
                result_parts.append(expanded)
        else:
            result_parts.append(ep['text'])

    final_text = '\n\n'.join(result_parts)
    job.final_text = final_text
    job.enhanced_map = expanded_map

    return StageResult(
        stage=Stage.EXPAND,
        output_preview='\n'.join(preview_lines),
        full_output={'expanded_text': final_text, 'expanded_map': expanded_map},
        metadata={'expanded_count': len(expanded_map)}
    )


def run_stage_enhance(job: Job) -> StageResult:
    """Enhancement stage - Claude polishes dialogue"""
    text = job.final_text
    episodes = parse_episodes(text)
    research_map = job.research_map
    suggestion = job.user_suggestions.get(Stage.ENHANCE, '')

    if not CLAUDE_API_KEY:
        return StageResult(
            stage=Stage.ENHANCE,
            output_preview="Claude API not configured. Skipping enhancement.",
            full_output={'enhanced_text': text},
            metadata={'enhanced': False}
        )

    all_changes = []
    enhanced_map = {}
    preview_lines = ["Claude enhancement complete:\n"]

    # Enhance each episode
    for ep in episodes:
        ep_num = ep['episode_num']
        research = research_map.get(ep_num, '')

        # Add user style guidance
        style_guidance = suggestion if suggestion else ""

        result = enhance_episode_with_claude({
            'episode_num': ep_num,
            'text': ep['text'],
            'research': research,
            'speakers': ['ALEX', 'SARAH'],
            'style_guidance': style_guidance
        })

        enhanced_map[ep_num] = result['enhanced_text']

        if result['success'] and result.get('changes'):
            changes = result['changes']
            all_changes.append({
                'episode': ep_num,
                'words_added': changes['words_added'],
                'words_removed': changes['words_removed'],
                'sample_additions': changes['sample_additions'][:2]
            })
            preview_lines.append(f"Episode {ep_num}: +{changes['words_added']} words")
            for sample in changes['sample_additions'][:1]:
                preview_lines.append(f"  \"{sample[:60]}...\"")

    # Rebuild text
    result_parts = []
    for ep in episodes:
        ep_num = ep['episode_num']
        if ep_num in enhanced_map:
            clean = re.sub(r'\[RESEARCH[^\]]*\].*?\[END[^\]]*\]', '', enhanced_map[ep_num], flags=re.DOTALL)
            result_parts.append(clean.strip())
        else:
            result_parts.append(ep['text'])

    final_text = '\n\n'.join(result_parts)
    job.final_text = final_text

    total_added = sum(c['words_added'] for c in all_changes)
    preview_lines.insert(1, f"Total: +{total_added} words across {len(all_changes)} episodes\n")

    return StageResult(
        stage=Stage.ENHANCE,
        output_preview='\n'.join(preview_lines),
        full_output={'enhanced_text': final_text},
        changes=all_changes,
        metadata={'total_words_added': total_added, 'episodes_enhanced': len(all_changes)}
    )


def run_stage_generate(job: Job) -> StageResult:
    """Generate stage - TTS audio generation"""
    text = job.final_text
    job_id = job.id
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    # Preprocess text
    processed = preprocess_text(text)

    # Split into chunks
    if job.multi_voice:
        speakers = detect_speakers(processed)
        if not speakers:
            speakers = ['ALEX', 'SARAH']
        voice_assignments = assign_speaker_voices(speakers)
        chunks = split_by_speaker(processed, voice_assignments, job.voice)
    else:
        chunks = split_into_chunks(processed)
        chunks = [(chunk, job.voice, None) for chunk in chunks]

    total_chunks = len(chunks)
    preview_lines = [f"Audio generation complete:\n"]
    preview_lines.append(f"  - {total_chunks} audio chunks generated")

    # Generate TTS (simplified for interactive mode - runs synchronously)
    chunk_files = []
    for idx, (chunk_text, chunk_voice, speaker) in enumerate(chunks):
        output_path = job_dir / f"chunk_{idx:04d}.mp3"
        try:
            generate_tts_audio(chunk_text, chunk_voice, job.model, output_path)
            chunk_files.append(output_path)
        except Exception as e:
            logger.error(f"TTS chunk {idx} failed: {e}")

    preview_lines.append(f"  - {len(chunk_files)} chunks successful")

    if job.multi_voice:
        preview_lines.append(f"  - Multi-voice: ALEX (echo) + SARAH (shimmer)")

    return StageResult(
        stage=Stage.GENERATE,
        output_preview='\n'.join(preview_lines),
        full_output={'chunk_files': chunk_files, 'total_chunks': total_chunks},
        metadata={'total_chunks': total_chunks, 'successful_chunks': len(chunk_files)}
    )


def run_stage_combine(job: Job) -> StageResult:
    """Combine stage - concatenate audio and save transcript"""
    job_id = job.id
    job_dir = TEMP_DIR / job_id

    generate_result = job.stage_results.get(Stage.GENERATE)
    if not generate_result:
        return StageResult(
            stage=Stage.COMBINE,
            output_preview="No audio chunks to combine.",
            full_output={},
            metadata={'success': False}
        )

    chunk_files = generate_result.full_output.get('chunk_files', [])

    # Concatenate MP3s
    output_path = job_dir / "podcast.mp3"
    concatenate_mp3_files(chunk_files, output_path)

    # Save transcript
    transcript_path = job_dir / 'transcript.txt'
    with open(transcript_path, 'w', encoding='utf-8') as f:
        f.write(f"# Podcast Transcript\n")
        f.write(f"# Generated: {datetime.now().isoformat()}\n")
        f.write(f"# Hosts: ALEX (male) and SARAH (female)\n")
        f.write(f"# Job ID: {job_id}\n\n")
        f.write(job.final_text)

    size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0
    smart_filename = generate_smart_filename(job.final_text)

    job.download_id = job_id
    job.transcript_id = job_id

    preview_lines = [
        "Pipeline complete!",
        "",
        f"Audio: {size_mb:.1f} MB",
        f"Filename: {smart_filename}",
        "",
        "Ready for download."
    ]

    return StageResult(
        stage=Stage.COMBINE,
        output_preview='\n'.join(preview_lines),
        full_output={'output_path': str(output_path), 'transcript_path': str(transcript_path)},
        metadata={'size_mb': size_mb, 'filename': smart_filename}
    )


def run_job_stage(job_id: str, stage: Stage):
    """Background worker to run a single pipeline stage"""
    job = job_store.get_job(job_id)
    if not job:
        logger.error(f"Job {job_id} not found")
        return

    job.current_stage = stage
    job.status = JobStatus.RUNNING
    job_store.update_job(job)

    logger.info(f"Job {job_id}: Starting stage {stage.value}")

    try:
        # Run appropriate stage processor
        if stage == Stage.ANALYZE:
            result = run_stage_analyze(job)
        elif stage == Stage.RESEARCH:
            result = run_stage_research(job)
        elif stage == Stage.EXPAND:
            result = run_stage_expand(job)
        elif stage == Stage.ENHANCE:
            result = run_stage_enhance(job)
        elif stage == Stage.GENERATE:
            result = run_stage_generate(job)
        elif stage == Stage.COMBINE:
            result = run_stage_combine(job)
        else:
            raise ValueError(f"Unknown stage: {stage}")

        # Store result
        job.stage_results[stage] = result

        # Determine next state
        if stage == Stage.COMBINE:
            job.status = JobStatus.COMPLETE
            logger.info(f"Job {job_id}: Pipeline complete")
        else:
            job.status = JobStatus.PAUSED_FOR_REVIEW
            logger.info(f"Job {job_id}: Paused for review after {stage.value}")

        job_store.update_job(job)

    except Exception as e:
        logger.error(f"Job {job_id}: Stage {stage.value} failed: {e}")
        job.status = JobStatus.ERROR
        job.error_message = str(e)
        job_store.update_job(job)


@app.route('/api/job', methods=['POST'])
@login_required
@limiter.limit("10 per hour")
def create_interactive_job():
    """Create a new interactive pipeline job"""
    # Extract request data
    if 'file' in request.files and request.files['file'].filename:
        file = request.files['file']
        text, error = validate_file_upload(file)
        if error:
            return jsonify({'error': error}), 400
    else:
        text = request.form.get('text', '')

    if not text.strip():
        return jsonify({'error': 'No text provided'}), 400

    if len(text) > MAX_TEXT_LENGTH:
        return jsonify({'error': f'Text too large. Maximum {MAX_TEXT_LENGTH//(1024*1024)}MB allowed.'}), 400

    # Create job
    job = Job(
        id=generate_job_id(),
        status=JobStatus.CREATED,
        current_stage=None,
        text=text,
        voice=request.form.get('voice', 'nova'),
        model=request.form.get('model', 'tts-1-hd'),
        multi_voice=request.form.get('multi_voice', 'true').lower() == 'true',
        ai_enhance=request.form.get('ai_enhance', 'true').lower() == 'true',
        auto_expand=request.form.get('auto_expand', 'true').lower() == 'true'
    )

    job_store.create_job(job)
    logger.info(f"Job {job.id}: Created interactive pipeline job")

    # Start first stage in background
    gevent.spawn(run_job_stage, job.id, Stage.ANALYZE)

    return jsonify({
        'job_id': job.id,
        'status': 'created',
        'message': 'Pipeline started. Poll /api/job/{job_id}/status for updates.'
    })


@app.route('/api/job/<job_id>/status')
@login_required
def get_interactive_job_status(job_id):
    """Get current job status and stage preview if paused"""
    job_id = re.sub(r'[^a-zA-Z0-9_-]', '', job_id)
    job = job_store.get_job(job_id)

    if not job:
        return jsonify({'error': 'Job not found'}), 404

    response = {
        'job_id': job.id,
        'status': job.status.value,
        'current_stage': job.current_stage.value if job.current_stage else None,
        'current_stage_name': Stage.get_display_name(job.current_stage) if job.current_stage else None,
        'awaiting_input': job.status == JobStatus.PAUSED_FOR_REVIEW
    }

    # Include stage preview if paused for review
    if job.status == JobStatus.PAUSED_FOR_REVIEW and job.current_stage:
        preview = job.get_stage_preview()
        if preview:
            # Return full preview object for frontend
            response['stage_preview'] = preview
            response['stage_name'] = preview.get('stage_name', '')

    # Include download info if complete
    if job.status == JobStatus.COMPLETE:
        response['download_id'] = job.download_id
        response['transcript_id'] = job.transcript_id
        combine_result = job.stage_results.get(Stage.COMBINE)
        if combine_result:
            response['filename'] = combine_result.metadata.get('filename', 'podcast.mp3')
            response['size_mb'] = combine_result.metadata.get('size_mb', 0)

    # Include error if failed
    if job.status == JobStatus.ERROR:
        response['error_message'] = job.error_message

    return jsonify(response)


@app.route('/api/job/<job_id>/continue', methods=['POST'])
@login_required
def continue_interactive_job(job_id):
    """Continue pipeline with optional user suggestion"""
    job_id = re.sub(r'[^a-zA-Z0-9_-]', '', job_id)
    job = job_store.get_job(job_id)

    if not job:
        return jsonify({'error': 'Job not found'}), 404

    if job.status != JobStatus.PAUSED_FOR_REVIEW:
        return jsonify({'error': 'Job not awaiting input', 'current_status': job.status.value}), 400

    # Get user suggestion
    data = request.get_json() or {}
    suggestion = data.get('suggestion', '').strip()

    # Store suggestion for NEXT stage (current stage is done)
    next_stage = Stage.get_next(job.current_stage)
    if next_stage and suggestion:
        job.user_suggestions[next_stage] = suggestion
        logger.info(f"Job {job_id}: User suggestion for {next_stage.value}: {suggestion[:50]}...")

    # Resume pipeline
    job.status = JobStatus.RUNNING
    job_store.update_job(job)

    if next_stage:
        gevent.spawn(run_job_stage, job.id, next_stage)
        return jsonify({
            'status': 'resumed',
            'next_stage': next_stage.value,
            'next_stage_name': Stage.get_display_name(next_stage),
            'suggestion_applied': bool(suggestion)
        })
    else:
        # Should not happen - combine is final
        job.status = JobStatus.COMPLETE
        job_store.update_job(job)
        return jsonify({'status': 'complete'})


@app.route('/api/job/<job_id>', methods=['DELETE'])
@login_required
def delete_interactive_job(job_id):
    """Delete a job and its files"""
    job_id = re.sub(r'[^a-zA-Z0-9_-]', '', job_id)

    # Delete from store
    job_store.delete_job(job_id)

    # Cleanup files
    job_dir = TEMP_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)

    return jsonify({'status': 'deleted'})


if __name__ == '__main__':
    # SECURITY: debug=False in production (use FLASK_DEBUG=1 for local dev)
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug_mode, port=5000)
