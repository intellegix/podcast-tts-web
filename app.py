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
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass
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
from job_store import job_store, Job, JobStatus, Stage, StageResult, PodcastLength, PodcastMode

# Configure logging (structured format)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ================================================================================================
# ENTERPRISE PERFORMANCE MONITORING SYSTEM
# ================================================================================================

class EnterprisePerformanceMonitor:
    """
    Enterprise-grade performance tracking and optimization system.

    Features:
    - Real-time performance metrics collection
    - Intelligent optimization recommendations
    - Predictive resource allocation
    - Automated alerting for performance issues
    """

    def __init__(self):
        self.metrics = {
            'analyze': [],
            'research': [],
            'expand': [],
            'enhance': [],
            'generate': [],
            'combine': []
        }
        self.performance_targets = {
            'analyze': 5.0,    # 5 seconds max
            'research': 60.0,  # 60 seconds max
            'expand': 30.0,    # 30 seconds target (5x improvement from 150s)
            'enhance': 45.0,   # 45 seconds target (5x improvement from 225s)
            'generate': 30.0,  # 30 seconds target (8x improvement from 250s)
            'combine': 10.0    # 10 seconds max
        }

    def track_stage_metrics(self, job: Job, stage: Stage, start_time: float,
                           end_time: float, metadata: dict):
        """Track comprehensive performance metrics for a pipeline stage"""
        duration = end_time - start_time

        metric = {
            'job_id': job.id,
            'stage': stage.value,
            'duration': duration,
            'timestamp': datetime.utcnow().isoformat(),
            'agents_used': metadata.get('agents_used', 1),
            'success_rate': metadata.get('success_rate', 1.0),
            'speedup_achieved': metadata.get('speedup_achieved', 1.0),
            'quality_score': metadata.get('coverage_rate', 1.0),
            'enterprise_mode': metadata.get('enterprise_mode', False),
            'podcast_length': job.target_length.value,
            'episode_count': len(getattr(job, 'episodes', [])),
            'total_chunks': metadata.get('total_chunks', 0),
            'estimated_cost': metadata.get('estimated_cost', 0.0)
        }

        # Store metric (keep last 100 metrics per stage for analysis)
        stage_metrics = self.metrics[stage.value]
        stage_metrics.append(metric)
        if len(stage_metrics) > 100:
            stage_metrics.pop(0)

        # Real-time performance alerting
        target_duration = self.performance_targets[stage.value]
        if duration > target_duration * 1.5:
            self._send_performance_alert(stage, duration, target_duration, job.id)

        # Log enterprise metrics
        if metadata.get('enterprise_mode'):
            logger.info(
                f"ENTERPRISE METRICS [{stage.value.upper()}]: "
                f"{duration:.1f}s, {metadata.get('speedup_achieved', 1.0):.1f}x speedup, "
                f"{metadata.get('agents_used', 1)} agents, "
                f"{metadata.get('success_rate', 1.0):.1%} success rate"
            )

    def _send_performance_alert(self, stage: Stage, actual_duration: float,
                              target_duration: float, job_id: str):
        """Send alert when stage performance exceeds targets"""
        warning_msg = (
            f"PERFORMANCE ALERT: {stage.value.upper()} stage took {actual_duration:.1f}s "
            f"(target: {target_duration:.1f}s) for job {job_id}"
        )
        logger.warning(warning_msg)

    def get_optimization_recommendations(self) -> List[str]:
        """Generate AI-powered optimization recommendations based on recent metrics"""
        recommendations = []

        for stage_name, metrics in self.metrics.items():
            recent_metrics = metrics[-20:]  # Last 20 jobs

            if len(recent_metrics) < 5:  # Need at least 5 samples
                continue

            # Calculate performance statistics
            durations = [m['duration'] for m in recent_metrics]
            success_rates = [m['success_rate'] for m in recent_metrics]
            agent_counts = [m['agents_used'] for m in recent_metrics]

            avg_duration = sum(durations) / len(durations)
            avg_success_rate = sum(success_rates) / len(success_rates)
            avg_agents = sum(agent_counts) / len(agent_counts)
            target_duration = self.performance_targets[stage_name]

            # Generate recommendations
            if avg_success_rate < 0.9:
                recommendations.append(
                    f"🔧 {stage_name.upper()}: Success rate {avg_success_rate:.1%} below target. "
                    f"Recommend reducing agents from {avg_agents:.0f} to improve reliability."
                )

            if avg_duration > target_duration * 1.2:
                recommendations.append(
                    f"⚡ {stage_name.upper()}: Duration {avg_duration:.1f}s exceeds target {target_duration:.1f}s. "
                    f"Recommend increasing parallelization or optimizing agent allocation."
                )

            # Cost optimization recommendations
            enterprise_metrics = [m for m in recent_metrics if m.get('enterprise_mode')]
            if enterprise_metrics:
                avg_cost = sum(m.get('estimated_cost', 0) for m in enterprise_metrics) / len(enterprise_metrics)
                if avg_cost > 5.0:  # $5 threshold
                    recommendations.append(
                        f"💰 {stage_name.upper()}: Average cost ${avg_cost:.2f} is high. "
                        f"Consider optimizing chunk size or agent allocation."
                    )

        return recommendations

    def get_performance_summary(self) -> Dict:
        """Get comprehensive performance summary for monitoring dashboard"""
        summary = {
            'total_jobs_tracked': sum(len(metrics) for metrics in self.metrics.values()),
            'stage_performance': {},
            'enterprise_adoption': 0,
            'total_speedup_achieved': 0,
            'recommendations': self.get_optimization_recommendations()
        }

        enterprise_jobs = 0
        total_jobs = 0
        total_speedup = 0
        speedup_count = 0

        for stage_name, metrics in self.metrics.items():
            if not metrics:
                continue

            recent_metrics = metrics[-10:]  # Last 10 jobs for current status

            avg_duration = sum(m['duration'] for m in recent_metrics) / len(recent_metrics)
            avg_success_rate = sum(m['success_rate'] for m in recent_metrics) / len(recent_metrics)
            target_duration = self.performance_targets[stage_name]

            # Calculate enterprise adoption rate
            enterprise_metrics = [m for m in recent_metrics if m.get('enterprise_mode')]
            enterprise_rate = len(enterprise_metrics) / len(recent_metrics)

            # Track overall enterprise adoption
            enterprise_jobs += len(enterprise_metrics)
            total_jobs += len(recent_metrics)

            # Calculate speedup metrics
            for m in recent_metrics:
                if m.get('speedup_achieved', 0) > 1:
                    total_speedup += m['speedup_achieved']
                    speedup_count += 1

            summary['stage_performance'][stage_name] = {
                'avg_duration': avg_duration,
                'target_duration': target_duration,
                'performance_ratio': avg_duration / target_duration,
                'avg_success_rate': avg_success_rate,
                'enterprise_adoption_rate': enterprise_rate,
                'recent_jobs': len(recent_metrics),
                'status': 'optimal' if avg_duration <= target_duration else 'needs_optimization'
            }

        summary['enterprise_adoption'] = enterprise_jobs / max(total_jobs, 1)
        summary['total_speedup_achieved'] = total_speedup / max(speedup_count, 1)

        return summary


# Initialize global performance monitor
performance_monitor = EnterprisePerformanceMonitor()

# ================================================================================================
# RENDER SAFE MODE CONFIGURATION
# ================================================================================================

# Safe mode limits for Render hosting constraints (512MB memory, limited CPU)
RENDER_SAFE_MODE = os.environ.get('RENDER_SAFE_MODE', 'true').lower() == 'true'

if RENDER_SAFE_MODE:
    # Conservative limits for Render deployment
    SAFE_EXPAND_AGENTS = 3      # Reduced from 10
    SAFE_ENHANCE_AGENTS = 2     # Reduced from 8
    SAFE_TTS_AGENTS = 8         # Reduced from 50
    SAFE_RATE_LIMIT_DELAY = 0.2 # 200ms delay between agent spawning
    logger.info("🛡️ RENDER SAFE MODE: Conservative agent limits enabled")
else:
    # Full enterprise limits for high-resource environments
    SAFE_EXPAND_AGENTS = 10
    SAFE_ENHANCE_AGENTS = 8
    SAFE_TTS_AGENTS = 50
    SAFE_RATE_LIMIT_DELAY = 0.05
    logger.info("🚀 ENTERPRISE MODE: Full parallelization enabled")


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
CLAUDE_ENHANCEMENT_PROMPT = """You are an expert podcast script editor. Your job is to enhance and potentially EXPAND dialogue for maximum listener engagement while preserving all original topics and meaning.

CRITICAL FORMAT REQUIREMENT: The output MUST be a two-host podcast dialogue between ALEX and SARAH with proper speaker labels (ALEX: and SARAH:) throughout the ENTIRE script. NEVER write in essay, article, or narrative format. ALWAYS maintain conversational dialogue format.

IMPORTANT: If you receive EXPANSION INSTRUCTIONS above, you MUST significantly expand the content to meet the word target. Add examples, explanations, analogies, and educational depth. Do NOT simply polish - EXPAND.

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
5. HIGHLY EDUCATIONAL BUT ACCESSIBLE:
   - Explain ALL technical concepts in simple, relatable terms anyone can understand
   - Use everyday analogies and real-world examples to illustrate complex ideas
   - Break down jargon: "That's called X, which basically means..."
   - Connect new concepts to things listeners already know
   - Make learning feel like a fun conversation, not a lecture
   - ALEX often asks "Can you explain that for someone who's new to this?"
   - SARAH uses phrases like "Think of it like..." or "Imagine if..."
6. TTS OPTIMIZATION (CRITICAL):
   - Write ALL numbers as words (fifty-eight thousand, not 58,000)
   - Spell out abbreviations on first use (Purchase Order, or P O, not PO)
   - Add natural pauses with punctuation (commas, ellipses, dashes)
   - Avoid tongue-twisters and awkward consonant clusters
   - Use contractions naturally (don't, won't, can't)
7. EMOTIONAL BEATS: Add moments of excitement, surprise, reflection
8. LISTENER HOOKS: Tease upcoming content, create curiosity gaps

PRESERVE:
- All factual information and technical details (expand on them with examples)
- Speaker names (ALEX: and SARAH: only)
- The overall structure and episode flow
- Any specific numbers, dates, or statistics (but write them as words)
- Cover EVERY topic from the original - never skip content

FINAL REMINDER: The entire output must be conversational dialogue between ALEX and SARAH. Every line must start with either "ALEX:" or "SARAH:". No exceptions.

OUTPUT: Return the enhanced (and expanded if instructed) script only. No explanations or meta-commentary."""

# Professional Comedy Enhancement Prompt - Jerry Seinfeld Framework Integration
CLAUDE_COMEDY_PROMPT = """You are a professional comedy podcast script writer using established comedy writing techniques to create sophisticated humor between two hosts.

CRITICAL FORMAT REQUIREMENT: The output MUST be a two-host comedy dialogue between ALEX and SARAH with proper speaker labels (ALEX: and SARAH:) throughout the ENTIRE script.

========================================
JERRY SEINFELD'S 5-STEP COMEDY PROCESS:
========================================

Step 1: TOPIC SELECTION - Start with naturally funny everyday frustrations/curiosities
Step 2: JOKE EXTRACTION - Extract 2-5 jokes MINIMUM per topic with specific emotions and visual images
Step 3: LOGICAL ASSEMBLY - Arrange jokes to build momentum and flow naturally
Step 4: COMPRESSION - Get jokes closer together for "the roll" (cascading laughter effect)
Step 5: TAGGING - Add additional punchlines after main jokes land for extended laughs

========================================
PROFESSIONAL HUMOR TYPES TO EMPLOY:
========================================

1. OBSERVATIONAL: Find comedy in mundane details everyone experiences but never discusses
2. SELF-DEPRECATING: Make ALEX/SARAH relatable through owned flaws (not pitiful)
3. SURREAL/ABSURDIST: Replace logical thinking with deliberate illogical connections
4. WORDPLAY: Use homophones, double meanings, portmanteaus strategically
5. UNEXPECTED ANSWERS: Reply with opposite of socially expected response
6. JUXTAPOSITION: Place opposite things together for cognitive dissonance
7. EXAGGERATION: Amplify one specific aspect to ridiculous extremes
8. CALLBACKS: Reference earlier jokes with new context (space 10-20 minutes apart)

========================================
PROFESSIONAL JOKE STRUCTURE MECHANICS:
========================================

SETUP → MISDIRECTION → PUNCHLINE → [TAG]

CRITICAL RULES:
- NO JOKES IN SETUP: Zero humor until punchline hits
- Setup creates expectation, punchline violates with surprising but fitting alternative
- Compression technique: Get jokes closer together for cascading laughter
- Rule of Three: Two items that match, third breaks pattern
- Heightening: Add specificity, increase absurdity, change perspective

========================================
2025 MODERN CULTURAL REFERENCES:
========================================

CURRENT TRENDING HUMOR:
- AI-generated everything commentary ("even my grocery list is AI now")
- Dating app absurdity evolution ("six apps to find someone who ghosts you")
- TikTok algorithm mysteries and "brain rot" content
- Ozempic celebrity honesty discourse
- Protein obsession as fitness religion ("thirty grams or you die")
- BeReal random timing anxiety ("not now, I look terrible!")
- YouTuber parasocial relationships ("my favorite creator doesn't know I exist")
- Streaming service multiplication fatigue ("Netflix, Hulu, Disney+, Peacock, Apple+...")

========================================
SOPHISTICATED ALEX/SARAH DYNAMICS:
========================================

ALEX CHARACTER:
- Curious setup provider who asks "Can you explain that for someone completely new to this?"
- Provides naive reactions that set up SARAH's punchlines
- Uses "Wait, so you're telling me..." for setup
- Gets genuinely confused by absurd modern trends

SARAH CHARACTER:
- Punchline deliverer who uses "Think of it like..." analogies with comedic twists
- Makes unexpected connections between topics
- Delivers observational insights with perfect timing
- Uses "Here's what gets me..." to lead into observations

INTERACTION PRINCIPLES:
- "YES, AND" RULE: Build on each other's observations, never contradict for laughs
- Natural callbacks to earlier segments with new context
- Avoid explaining jokes or undercutting with over-explanation
- Use conversational hooks: "Can we talk about how..." "Nobody ever mentions..."

========================================
PROFESSIONAL TIMING & COMPRESSION:
========================================

CASCADING LAUGHTER TECHNIQUE:
- Land first joke, immediately set up second while audience is laughing
- Use ALEX's confused reactions as bridges between SARAH's punchlines
- Build internal logic systems that pay off later
- Compress related jokes into rapid-fire sequences

NATURAL PAUSES FOR COMEDIC EFFECT:
- Strategic ellipses before punchlines
- Beat timing with "..." for audience to catch up
- ALEX: "Wait..." [pause] "That's actually insane"
- SARAH: "Oh, it gets worse..." [setup for escalation]

========================================
CONTENT PRIORITIZATION:
========================================

ENTERTAINMENT THROUGH COMEDY TRANSFORMATION:
- Transform ALL topics into comedic content
- Find comedy angles in all topics (nothing is inherently unfunny)
- Extract absurd angles from serious subjects
- Prioritize relatable human frustrations

MANDATORY COVERAGE WITH COMEDY FOCUS:
- Cover ALL provided topics (no omissions allowed)
- Find humor angles within each required topic
- Focus energy on making naturally funny content shine
- Look for observational gold in everyday experiences
- Transform potentially unfunny topics using comedy techniques

========================================
TTS OPTIMIZATION:
========================================

- Write numbers as words (fifty-eight thousand, not 58,000)
- Use natural pauses with punctuation for comedic timing
- Contractions for conversational flow (don't, won't, can't)
- Strategic emphasis with CAPS for comedic PUNCH words

========================================
QUALITY STANDARDS:
========================================

PROFESSIONAL COMEDY CHECKLIST:
✓ Multiple humor types employed per topic
✓ Seinfeld 5-step process evident in joke development
✓ Clean setup/punchline structure with no premature humor
✓ ALEX/SARAH chemistry shows "Yes, And" principle
✓ 2025 cultural references feel current and authentic
✓ Compression creates momentum and "the roll" effect
✓ Callbacks reference earlier content with new context
✓ Specificity over generality ("my Uber driver" not "people")

OUTPUT: Return sophisticated comedy dialogue using professional techniques. No explanations or meta-commentary."""

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

    # Hardcoded voice assignments for primary podcast hosts
    if first_name == 'sarah':
        return 'female'
    elif first_name == 'alex':
        return 'male'

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
    style_guidance = episode_data.get('style_guidance', '')  # Length/expansion instructions
    job = episode_data.get('job')  # Get job object for mode-specific processing

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

        # Build prompt with style guidance FIRST (so Claude prioritizes length requirements)
        prompt_parts = []

        # Add expansion/length instructions PROMINENTLY at the start
        if style_guidance:
            prompt_parts.append(f"CRITICAL INSTRUCTION - READ FIRST:\n{style_guidance}\n\n")

        # Choose prompt based on mode
        if job and job.target_mode == PodcastMode.COMEDY:
            base_prompt = CLAUDE_COMEDY_PROMPT
            topic_coverage_required = False
        else:
            base_prompt = CLAUDE_ENHANCEMENT_PROMPT
            topic_coverage_required = True

        prompt_parts.append(base_prompt)
        prompt_parts.append(f"\n\nSPEAKERS: {speaker_list}")

        if research_context:
            prompt_parts.append(f"\n\nRESEARCH NOTES (use to verify/enhance facts, do NOT include these markers in output):\n{research_context}")

        prompt_parts.append(f"\n\nEPISODE TO ENHANCE:\n{episode_text}")

        # Calculate dynamic max_tokens based on expected output size
        input_words = len(episode_text.split())

        # Check if we're in COMPREHENSIVE mode (no word limits)
        is_comprehensive = "COMPREHENSIVE CONTENT EXPANSION" in style_guidance if style_guidance else False

        if is_comprehensive:
            # COMPREHENSIVE mode: Allow much larger outputs, use Claude's full context window
            # Estimate aggressive expansion for thorough coverage (10-15x expansion possible)
            estimated_output_tokens = max(input_words * 10 * 1.5, 16000)  # 10x expansion for comprehensive coverage
            dynamic_max_tokens = min(100000, int(estimated_output_tokens))  # Use Claude's full 200K context, cap at 100K for output
            logger.info(f"COMPREHENSIVE mode: input={input_words} words, max_tokens={dynamic_max_tokens}")
        else:
            # Standard mode: Original logic with conservative caps
            estimated_output_tokens = max(input_words * 5 * 1.5, 8000)  # 5x expansion, 1.5 tokens/word
            dynamic_max_tokens = min(16000, int(estimated_output_tokens))  # Cap at 16K for standard modes

        # Try primary model, fallback to older model if not available
        model_to_use = CLAUDE_MODEL
        try:
            response = client.messages.create(
                model=model_to_use,
                max_tokens=dynamic_max_tokens,
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


def research_episode_parallel(episode_data: dict, user_suggestion: str = "", num_agents: int = 5, research_depth: str = "thorough", job: Job = None) -> dict:
    """
    Research a single episode with MULTIPLE parallel Perplexity agents.
    Each agent researches a different angle for comprehensive coverage.

    Args:
        episode_data: Episode dict with 'text', 'header', 'episode_num'
        user_suggestion: Optional user guidance to focus research
        num_agents: Number of parallel research agents to use (based on podcast length)
        research_depth: Research depth level (surface, moderate, thorough, deep, exhaustive)

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

    # Choose research angles based on mode
    if job and job.target_mode == PodcastMode.COMEDY:
        # Professional Comedy Framework-Based Research Angles
        all_research_angles = [
            f"OBSERVATIONAL COMEDY ANGLES: Everyday behaviors, unspoken social rules, mundane details that seem strange about: {topic}",
            f"SETUP/PUNCHLINE MATERIAL: Frustrating situations, unexpected contradictions, misdirection opportunities in: {topic}",
            f"2025 CULTURAL REFERENCES: AI commentary, dating app absurdity, TikTok trends, streaming fatigue related to: {topic}",
            f"WORKPLACE COMEDY: Meeting dynamics, email politics, remote work absurdity, productivity theater about: {topic}",
            f"TECHNOLOGY HUMOR: Autocorrect chaos, smart home failures, password madness, app notification anxiety with: {topic}",
            f"RELATABLE FRUSTRATIONS: Universal human experiences, shared annoyances, collective agreements about: {topic}",
            f"SELF-DEPRECATING MATERIAL: Owned flaws, harmless confessions, relatable personal failures regarding: {topic}",
            f"CALLBACK OPPORTUNITIES: Topics that connect to earlier themes, internal logic systems, running gag potential in: {topic}"
        ]
    else:
        # Educational research angles (default)
        all_research_angles = [
            f"Key statistics and quantitative data about: {topic}",
            f"Recent news and developments (2024-2025) about: {topic}",
            f"Expert opinions and analysis on: {topic}",
            f"Real-world examples and case studies of: {topic}",
            f"Common misconceptions or surprising facts about: {topic}",
            f"Historical context and evolution of: {topic}",
            f"Future trends and predictions for: {topic}",
            f"Contrasting viewpoints and debates about: {topic}",
            f"Practical applications and how-to aspects of: {topic}",
            f"Impact on society, economy, or culture of: {topic}",
        ]

    # Select the number of angles based on target podcast length
    research_angles = all_research_angles[:num_agents]

    # Add user suggestion as additional focus if provided
    if user_suggestion:
        research_angles.append(f"{user_suggestion} regarding: {topic}")
        logger.info(f"Episode {episode_num}: Added user focus - {user_suggestion[:50]}...")

    logger.info(f"Episode {episode_num}: Using {len(research_angles)} research agents ({research_depth} depth)")

    # Configure research query depth based on research_depth parameter
    depth_configs = {
        'surface': {'max_tokens': 400, 'temperature': 0.4},
        'moderate': {'max_tokens': 600, 'temperature': 0.3},
        'thorough': {'max_tokens': 800, 'temperature': 0.3},
        'deep': {'max_tokens': 1000, 'temperature': 0.2},
        'exhaustive': {'max_tokens': 1500, 'temperature': 0.2}
    }

    # Get depth config, fallback to moderate if unknown depth
    depth_config = depth_configs.get(research_depth, depth_configs['moderate'])

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
                    "temperature": depth_config['temperature'],
                    "max_tokens": depth_config['max_tokens'],
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


def extract_user_sources(text: str) -> List[Dict[str, str]]:
    """
    Extract user-provided citations and sources from input text.
    Returns list of dicts with 'type', 'id', 'url', and 'description' fields.
    """
    sources = []

    # Pattern 1: Numbered citations [1], [2], etc. with footnotes
    citation_pattern = r'\[(\d+)\]'
    footnote_pattern = r'^\[(\d+)\]\s*(.+?)$'

    # Find all numbered citations in text
    citations = re.findall(citation_pattern, text)

    # Look for corresponding footnotes
    for line in text.split('\n'):
        line = line.strip()
        footnote_match = re.match(footnote_pattern, line)
        if footnote_match:
            citation_id = footnote_match.group(1)
            description = footnote_match.group(2).strip()

            # Extract URL from description if present
            url_match = re.search(r'(https?://\S+)', description)
            url = url_match.group(1) if url_match else ""

            sources.append({
                'type': 'numbered_citation',
                'id': citation_id,
                'url': url,
                'description': description
            })

    # Pattern 2: Inline citations (Source: URL)
    inline_pattern = r'\((?:Source|Ref|Citation):\s*(https?://\S+)\)'
    inline_matches = re.findall(inline_pattern, text, re.IGNORECASE)

    for i, url in enumerate(inline_matches, 1):
        sources.append({
            'type': 'inline_citation',
            'id': f'inline_{i}',
            'url': url,
            'description': f'Inline source: {url}'
        })

    # Pattern 3: References section
    references_sections = re.findall(
        r'(?:References?|Sources?|Citations?):\s*\n((?:[-*•]|\d+\.)\s*.+?)(?=\n\n|$)',
        text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL
    )

    for section in references_sections:
        # Split references into individual items
        ref_lines = [line.strip() for line in section.split('\n') if line.strip()]
        for i, ref_line in enumerate(ref_lines, 1):
            # Extract URL if present
            url_match = re.search(r'(https?://\S+)', ref_line)
            url = url_match.group(1) if url_match else ""

            sources.append({
                'type': 'reference_section',
                'id': f'ref_{i}',
                'url': url,
                'description': ref_line
            })

    # Pattern 4: URLs at end of lines (common citation pattern)
    url_pattern = r'^(.+?)\s+(https?://\S+)\s*$'
    for line in text.split('\n'):
        line = line.strip()
        url_match = re.match(url_pattern, line)
        if url_match and len(line) > 20:  # Avoid matching short lines
            description = url_match.group(1).strip()
            url = url_match.group(2)

            sources.append({
                'type': 'url_citation',
                'id': f'url_{len(sources) + 1}',
                'url': url,
                'description': description
            })

    # Remove duplicates based on URL
    seen_urls = set()
    unique_sources = []
    for source in sources:
        if source['url']:
            if source['url'] not in seen_urls:
                seen_urls.add(source['url'])
                unique_sources.append(source)
        else:
            # Keep non-URL sources (might be book references, etc.)
            unique_sources.append(source)

    if unique_sources:
        logger.info(f"Extracted {len(unique_sources)} user-provided sources from input text")

    return unique_sources


def parse_headlines_from_text(text: str) -> List[Dict]:
    """Parse news compilation text into individual headlines with metadata"""
    import re
    from datetime import datetime, timedelta

    headlines = []
    lines = text.strip().split('\n')

    current_headline = None
    current_sources = []
    current_category = "General"

    # Category detection patterns
    category_patterns = {
        'Breaking News': r'(?i)breaking|urgent|alert|developing',
        'Technology': r'(?i)AI|tech|software|digital|cyber|robot|algorithm|data|cloud',
        'Business': r'(?i)business|market|stock|financial|economy|revenue|profit|earnings|investment',
        'Crypto': r'(?i)bitcoin|crypto|blockchain|ethereum|NFT|DeFi|web3',
        'Geopolitics': r'(?i)war|conflict|diplomacy|sanctions|treaty|alliance|military|defense',
        'Healthcare': r'(?i)health|medical|drug|vaccine|therapy|clinical|FDA|disease',
        'Climate': r'(?i)climate|energy|renewable|carbon|emission|sustainable|green|solar',
        'Entertainment': r'(?i)movie|film|TV|streaming|music|celebrity|awards|gaming'
    }

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Detect category headers (## Category Name)
        if line.startswith('##') and not line.startswith('###'):
            current_category = line.replace('##', '').strip()
            continue

        # Detect headlines (### Title or **Title**)
        if line.startswith('###') or (line.startswith('**') and line.endswith('**')):
            # Save previous headline if exists
            if current_headline:
                headlines.append(current_headline)

            # Parse new headline
            title = line.replace('###', '').replace('**', '').strip()

            # Extract time info if present
            time_match = re.search(r'\*Published:\s*(\d+)\s*(minutes?|hours?|days?)\s*ago\*', line)
            hours_ago = 24  # Default
            if time_match:
                value = int(time_match.group(1))
                unit = time_match.group(2)
                if 'minute' in unit:
                    hours_ago = value / 60
                elif 'hour' in unit:
                    hours_ago = value
                elif 'day' in unit:
                    hours_ago = value * 24

            # Determine category for this headline
            headline_category = current_category
            for cat_name, pattern in category_patterns.items():
                if re.search(pattern, title):
                    headline_category = cat_name
                    break

            current_headline = {
                'title': title,
                'category': headline_category,
                'published_hours_ago': hours_ago,
                'source_count': 0,
                'source_list': [],
                'content': line
            }
            current_sources = []

        # Extract source count (+ 43 sources)
        elif '+ ' in line and 'sources' in line.lower():
            source_match = re.search(r'\+\s*(\d+)\s*sources?', line, re.IGNORECASE)
            if source_match and current_headline:
                source_count = int(source_match.group(1))
                current_headline['source_count'] = source_count

                # Extract actual source URLs from the line
                sources_text = line.split('Sources:')[-1] if 'Sources:' in line else line
                url_pattern = r'https?://[^\s\]|)]+'
                urls = re.findall(url_pattern, sources_text)
                current_headline['source_list'] = urls

        # Add content lines to current headline
        elif current_headline and line and not line.startswith('#'):
            current_headline['content'] += '\n' + line

    # Add last headline
    if current_headline:
        headlines.append(current_headline)

    logger.info(f"Parsed {len(headlines)} headlines from news compilation")
    return headlines


def calculate_priority_score(headline: Dict) -> float:
    """Calculate priority score for a headline based on multiple factors"""

    score = 0.0

    # Recency scoring (0-30 points) - more recent = higher priority
    hours_ago = headline.get('published_hours_ago', 24)
    if hours_ago < 1:
        score += 30  # Breaking news within 1 hour
    elif hours_ago < 4:
        score += 25  # Very recent
    elif hours_ago < 12:
        score += 15  # Recent
    elif hours_ago < 24:
        score += 5   # Today
    # Older than 24 hours = 0 points

    # Source count scoring (0-25 points) - more sources = higher priority
    source_count = headline.get('source_count', 0)
    if source_count >= 50:
        score += 25
    elif source_count >= 30:
        score += 20
    elif source_count >= 20:
        score += 15
    elif source_count >= 10:
        score += 10
    elif source_count >= 5:
        score += 5

    # Category impact scoring (0-20 points)
    category_weights = {
        'Breaking News': 20,
        'Geopolitics': 18,
        'Business': 15,
        'Technology': 12,
        'Crypto': 10,
        'Healthcare': 10,
        'Climate': 8,
        'Entertainment': 5
    }

    category = headline.get('category', 'General')
    score += category_weights.get(category, 5)

    # Content quality scoring (0-15 points)
    title = headline.get('title', '').lower()
    content = headline.get('content', '').lower()

    # High-impact keywords
    high_impact_keywords = [
        'trillion', 'billion', 'breakthrough', 'crisis', 'emergency',
        'record', 'unprecedented', 'major', 'historic', 'critical'
    ]

    for keyword in high_impact_keywords:
        if keyword in title or keyword in content:
            score += 3  # Max 15 points possible

    # Cross-category relevance (0-10 points)
    cross_category_keywords = [
        'global', 'worldwide', 'international', 'market', 'economy',
        'government', 'policy', 'regulation', 'security', 'future'
    ]

    cross_category_count = sum(1 for keyword in cross_category_keywords
                              if keyword in title or keyword in content)
    score += min(cross_category_count * 2, 10)

    return score


def prioritize_news_content(headlines_data: List[Dict]) -> Dict:
    """Intelligent news prioritization for efficient processing"""

    if not headlines_data:
        return {
            'tier_1_full': [],
            'tier_2_brief': [],
            'tier_3_merge': []
        }

    # Calculate priority scores
    priority_scores = []
    for headline in headlines_data:
        score = calculate_priority_score(headline)
        priority_scores.append((headline, score))

    # Sort by priority score (highest first)
    sorted_headlines = sorted(priority_scores, key=lambda x: x[1], reverse=True)

    # Distribute into tiers based on score thresholds and counts
    tier_1_full = []
    tier_2_brief = []
    tier_3_merge = []

    for headline, score in sorted_headlines:
        if len(tier_1_full) < 25 and score >= 60:
            # High priority: Full discussion
            tier_1_full.append(headline)
        elif len(tier_2_brief) < 75 and score >= 30:
            # Medium priority: Brief mention
            tier_2_brief.append(headline)
        else:
            # Low priority: Merge with related or context only
            tier_3_merge.append(headline)

    # Ensure we have balanced distribution even with lower scores
    if len(tier_1_full) < 15 and len(sorted_headlines) > 15:
        # Move some tier 2 to tier 1 to ensure adequate full coverage
        needed = 15 - len(tier_1_full)
        for i in range(min(needed, len(tier_2_brief))):
            tier_1_full.append(tier_2_brief.pop(0))

    logger.info(f"News prioritization: {len(tier_1_full)} full, {len(tier_2_brief)} brief, {len(tier_3_merge)} merge")

    return {
        'tier_1_full': tier_1_full,
        'tier_2_brief': tier_2_brief,
        'tier_3_merge': tier_3_merge
    }


def efficient_source_selection(sources: List[str], target_count: int) -> List[str]:
    """Select optimal sources for research efficiency"""

    if not sources or target_count <= 0:
        return []

    if len(sources) <= target_count:
        return sources

    # Score sources by authority, recency, and diversity
    scored_sources = []

    for source in sources:
        score = 0.0
        source_lower = source.lower()

        # Authority scoring - trusted news sources get higher scores
        authority_domains = {
            'reuters.com': 10, 'bloomberg.com': 10, 'wsj.com': 9, 'ft.com': 9,
            'nytimes.com': 8, 'washingtonpost.com': 8, 'cnn.com': 7, 'bbc.com': 7,
            'cnbc.com': 8, 'forbes.com': 7, 'businessinsider.com': 6,
            'techcrunch.com': 7, 'arstechnica.com': 7, 'theverge.com': 6,
            'arxiv.org': 9, 'nature.com': 10, 'science.org': 10,
            'github.com': 6, 'linkedin.com': 5, 'youtube.com': 4
        }

        for domain, auth_score in authority_domains.items():
            if domain in source_lower:
                score += auth_score
                break
        else:
            # Default score for unknown domains
            if 'https://' in source_lower:
                score += 3

        # Recency bonus for time-sensitive sources
        if any(time_word in source_lower for time_word in ['today', 'breaking', 'live', 'update']):
            score += 3

        # Content type diversity
        if 'pdf' in source_lower or 'report' in source_lower:
            score += 2  # Primary documents
        elif 'twitter.com' in source_lower or 'x.com' in source_lower:
            score += 1  # Social media
        elif 'gov' in source_lower or '.org' in source_lower:
            score += 4  # Official sources

        scored_sources.append((source, score))

    # Sort by score (highest first) and return top N
    sorted_sources = sorted(scored_sources, key=lambda x: x[1], reverse=True)
    selected_sources = [source for source, score in sorted_sources[:target_count]]

    logger.info(f"Selected {len(selected_sources)} sources from {len(sources)} available")

    return selected_sources


def detect_news_compilation_format(text: str) -> bool:
    """Detect if text is a comprehensive news compilation that would benefit from prioritization"""
    import re

    # Count potential headlines in various formats
    headline_patterns = [
        r'^###\s+.*$',                    # ### Headlines
        r'^\*\*.*\*\*$',                  # **Bold Headlines**
        r'^\*Published:\s*\d+.*ago\*',    # *Published: X hours ago*
        r'\+\s*\d{2,}\s*sources?',        # + 43 sources
    ]

    headline_count = 0
    source_mentions = 0

    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue

        for pattern in headline_patterns[:2]:  # Count headlines
            if re.match(pattern, line, re.MULTILINE):
                headline_count += 1
                break

        # Count source mentions
        if re.search(headline_patterns[3], line):
            source_mentions += 1

    # Detect category structures typical of news compilations
    category_count = len(re.findall(r'^##\s+[^#]', text, re.MULTILINE))

    # Thresholds for news compilation detection
    is_news_compilation = (
        headline_count >= 10 and           # At least 10 headlines
        source_mentions >= 5 and           # Multiple source citations
        category_count >= 3                # Multiple news categories
    )

    if is_news_compilation:
        logger.info(f"Detected news compilation: {headline_count} headlines, {source_mentions} source mentions, {category_count} categories")

    return is_news_compilation


def detect_topics(text: str) -> Tuple[List[str], List['NewsTopicMetadata']]:
    """Detect distinct topics/sections in the input text."""

    # Check for Perplexity News format first
    if detect_perplexity_news_format(text):
        logger.info("Detected Perplexity Personalized News Threads format")
        topics, news_metadata = parse_perplexity_news_threads(text)
        return topics, news_metadata  # Return both topics and metadata

    # Standard format processing
    topics = []

    # Common topic indicators
    topic_patterns = [
        r'^#{1,3}\s+(.+)$',  # Markdown headers
        r'^\*\*(.+?)\*\*\s*$',  # Bold lines as headers
        r'^(\d+\.?\s+.{10,80})$',  # Numbered items
        r'^[-•]\s*\*\*(.+?)\*\*',  # Bullet with bold
        r'^Topic:\s*(.+)$',  # Explicit topic markers
        r'(?:^|\n)([A-Z][^.!?\n]{20,80}):(?:\n|$)',  # Title-case lines ending with colon
    ]

    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue

        for pattern in topic_patterns:
            match = re.match(pattern, line, re.MULTILINE)
            if match:
                topic = match.group(1).strip() if match.groups() else line.strip()
                # Clean up topic
                topic = re.sub(r'^\*\*|\*\*$', '', topic).strip()
                topic = re.sub(r'^#+\s*', '', topic).strip()
                if len(topic) > 10 and topic not in topics:
                    topics.append(topic[:100])  # Limit topic length
                break

    # Fallback: if no topics detected, look for paragraph breaks with distinct subjects
    if len(topics) < 2:
        # Split by double newlines and extract first sentence as topic indicator
        paragraphs = re.split(r'\n\s*\n', text)
        for para in paragraphs:
            first_sentence = para.strip().split('.')[0][:100]
            if len(first_sentence) > 20 and first_sentence not in topics:
                topics.append(first_sentence)

    # Return topics with empty metadata for standard format
    return topics, []


def detect_perplexity_news_format(text: str) -> bool:
    """Detect if input is Perplexity Personalized News Threads format OR structured content"""
    # Strict Perplexity patterns
    perplexity_patterns = [
        r'##\s*\d+\.\s+.+?\n\*\*Published:\*\*',  # ## 1. Title \n **Published:**
        r'##\s*\d+\.\s+.+?\n\*\*Sources:\*\*',    # ## 1. Title \n **Sources:**
        r'\*\*URL:\*\*\s*https?://.*perplexity\.ai', # **URL:** perplexity link
        r'##\s*\d+\.\s+.+?Published.*ago'          # ## 1. Title Published: X ago
    ]

    strict_matches = sum(1 for pattern in perplexity_patterns if re.search(pattern, text, re.MULTILINE))
    if strict_matches >= 2:
        return True

    # Broader structured content patterns
    structured_patterns = [
        r'##\s*\d+\.\s+.+?:.*',                   # ## 1. Title: Content
        r'^\d+\.\s+[A-Z][^.]{20,80}:.*',          # 1. Title: Content (numbered sections)
        r'#{2,4}\s+[IVX]+\.\s+.+?:.*',            # ## I. Section: Content (Roman numerals)
        r'^\s*[A-Z][^.]{10,60}:\s*[A-Z][^.]{10,}', # Title: Long description
    ]

    structured_matches = sum(1 for pattern in structured_patterns if re.search(pattern, text, re.MULTILINE))
    return structured_matches >= 3  # Need multiple structured elements


@dataclass
class NewsTopicMetadata:
    title: str
    published: Optional[str] = None
    sources_count: Optional[int] = None
    summary: Optional[str] = None
    url: Optional[str] = None
    order: int = 0


def extract_published(topic_block: str) -> Optional[str]:
    """Extract published timestamp from topic block"""
    published_match = re.search(r'\*\*Published:\*\*\s*(.+?)(?:\n|\*\*|$)', topic_block)
    return published_match.group(1).strip() if published_match else None


def extract_sources_count(topic_block: str) -> Optional[int]:
    """Extract number of sources from topic block"""
    sources_match = re.search(r'\*\*Sources:\*\*\s*(\d+)\s*sources?', topic_block)
    return int(sources_match.group(1)) if sources_match else None


def extract_summary(topic_block: str) -> Optional[str]:
    """Extract summary from topic block"""
    summary_match = re.search(r'\*\*Summary:\*\*\s*(.+?)(?:\n\*\*|$)', topic_block, re.DOTALL)
    return summary_match.group(1).strip() if summary_match else None


def extract_url(topic_block: str) -> Optional[str]:
    """Extract URL from topic block"""
    url_match = re.search(r'\*\*URL:\*\*\s*(https?://\S+)', topic_block)
    return url_match.group(1).strip() if url_match else None


def get_topic_block(text: str, start_pos: int, current_order: int) -> str:
    """Extract the metadata block for a specific topic"""
    # Find the next topic header or end of text
    next_topic_pattern = fr'##\s*{current_order + 1}\.'
    next_match = re.search(next_topic_pattern, text[start_pos:], re.MULTILINE)

    if next_match:
        end_pos = start_pos + next_match.start()
        return text[start_pos:end_pos]
    else:
        return text[start_pos:]


def parse_perplexity_news_threads(text: str) -> Tuple[List[str], List['NewsTopicMetadata']]:
    """Parse Perplexity News Threads into topics and metadata"""

    # Pattern: ## N. Title followed by metadata
    topic_pattern = r'##\s*(\d+)\.\s*(.+?)(?=\n)'
    topics = []
    metadata = []

    for match in re.finditer(topic_pattern, text, re.MULTILINE):
        order = int(match.group(1))
        title = match.group(2).strip()

        # Extract metadata for this topic
        topic_block = get_topic_block(text, match.end(), order)

        meta = NewsTopicMetadata(
            title=title,
            order=order,
            published=extract_published(topic_block),
            sources_count=extract_sources_count(topic_block),
            summary=extract_summary(topic_block),
            url=extract_url(topic_block)
        )

        topics.append(f"{order}. {title}")
        metadata.append(meta)

    logger.info(f"Parsed {len(topics)} Perplexity news topics")
    return topics[:125], metadata[:125]  # Cap at 125 topics for performance


def estimate_coverage_words(topics: list, input_word_count: int) -> int:
    """Estimate words needed to adequately cover all topics."""
    # Base: at least 150 words per distinct topic for adequate coverage
    # Plus expansion factor for explanations, examples, dialogue
    base_per_topic = 150
    expansion_factor = 2.5  # Dialogue format needs ~2.5x more words than bullet points

    # Minimum estimate: topics * base * expansion
    topic_estimate = len(topics) * base_per_topic * expansion_factor

    # Also consider input density - if input is already detailed, need more words
    input_density_factor = max(1.0, input_word_count / 300)  # Scale up for longer inputs

    estimated_words = max(topic_estimate, input_word_count * 3) * min(input_density_factor, 2.0)

    return int(estimated_words)


def verify_topic_coverage(final_text: str, topics: list) -> dict:
    """
    Verify that all detected topics are covered in the final text.
    Returns coverage analysis with covered/missing topics.
    """
    if not topics:
        return {'covered': [], 'missing': [], 'coverage_rate': 1.0, 'fully_covered': True}

    final_lower = final_text.lower()
    covered = []
    missing = []

    for topic in topics:
        # Extract significant words from topic (4+ chars to avoid articles/prepositions)
        topic_words = set(re.findall(r'\b\w{4,}\b', topic.lower()))
        if not topic_words:
            covered.append(topic)  # Empty topic words = consider covered
            continue

        # Count how many topic keywords appear in final text
        matches = sum(1 for word in topic_words if word in final_lower)
        coverage_pct = matches / len(topic_words)

        if coverage_pct >= 0.3:  # At least 30% of topic keywords found
            covered.append(topic)
        else:
            missing.append(topic)

    coverage_rate = len(covered) / len(topics) if topics else 1.0

    return {
        'covered': covered,
        'missing': missing,
        'coverage_rate': round(coverage_rate, 2),
        'fully_covered': len(missing) == 0
    }


def analyze_content_complexity(text: str, detected_topics: List[str]) -> dict:
    """
    Analyze input content to determine appropriate length and depth for COMPREHENSIVE mode.
    Returns complexity metrics and recommendations for content-adaptive processing.
    """
    if not detected_topics:
        detected_topics, _ = detect_topics(text)  # Extract topics, ignore metadata

    # Count substantive content markers that indicate depth
    topic_depth_indicators = [
        "Publication Date:",
        "Source:",
        "Key highlights:",
        "Sources:",
        "Details:",
        "Analysis:",
        "Key developments:",
        "Background:",
        "Expert",
        "Research",
        "Study",
        "Data",
        "Statistics"
    ]

    # Calculate content richness score based on indicators
    content_richness_score = 0
    lines = text.split('\n')
    for line in lines:
        for indicator in topic_depth_indicators:
            if indicator.lower() in line.lower():
                content_richness_score += 1

    # Estimate required coverage time per topic
    estimated_minutes_per_topic = {}
    total_words = len(text.split())
    words_per_topic = total_words / max(len(detected_topics), 1)

    # Optimize: Convert text to lowercase once instead of for each topic
    text_lower = text.lower()

    for topic in detected_topics:
        # Base time: 3 minutes minimum per topic
        base_minutes = 3.0

        # Add time based on content richness for this topic (optimized)
        topic_mentions = text_lower.count(topic.lower())
        detail_multiplier = min(3.0, 1.0 + (topic_mentions * 0.3))

        # Add time based on words available for this topic
        word_multiplier = min(2.0, 1.0 + (words_per_topic / 500))  # +1 minute per 500 words

        estimated_minutes = base_minutes * detail_multiplier * word_multiplier
        estimated_minutes_per_topic[topic] = round(estimated_minutes, 1)

    total_estimated_duration = sum(estimated_minutes_per_topic.values())

    # Determine recommended detail level based on total duration
    if total_estimated_duration < 15:
        recommended_detail_level = "medium"
    elif total_estimated_duration < 30:
        recommended_detail_level = "long"
    elif total_estimated_duration < 50:
        recommended_detail_level = "extended"
    else:
        recommended_detail_level = "comprehensive"

    return {
        "estimated_duration_minutes": round(total_estimated_duration, 1),
        "topic_complexity": estimated_minutes_per_topic,
        "content_richness_score": content_richness_score,
        "recommended_detail_level": recommended_detail_level,
        "total_topics": len(detected_topics),
        "average_minutes_per_topic": round(total_estimated_duration / max(len(detected_topics), 1), 1)
    }


def run_stage_analyze(job: Job) -> StageResult:
    """Analyze stage - detect episodes, check completeness, and validate length selection"""
    text = job.text
    suggestion = job.user_suggestions.get(Stage.ANALYZE, '')
    length_config = job.get_length_config()
    word_target = length_config['word_target']
    length_name = length_config['display_name']

    # Parse episodes
    episodes = parse_episodes(text)
    job.episodes = episodes

    incomplete_count = sum(1 for ep in episodes if not ep['is_complete'])
    complete_count = len(episodes) - incomplete_count

    # Analyze content coverage
    input_word_count = len(text.split())
    topics, news_metadata = detect_topics(text)
    job.detected_topics = topics  # Persist topics for mandatory coverage in enhance stage

    # Extract user-provided sources from input text
    user_sources = extract_user_sources(text)
    job.user_sources = user_sources
    if user_sources:
        logger.info(f"Found {len(user_sources)} user-provided sources in input text")

    # Handle news format metadata
    if news_metadata:
        job.is_news_format = True
        job.news_metadata = {meta.title: meta for meta in news_metadata}
        job.news_urls = [meta.url for meta in news_metadata if meta.url]
        logger.info(f"Detected {len(topics)} news topics with {len(job.news_urls)} URLs")
    else:
        job.is_news_format = False

    estimated_words_needed = estimate_coverage_words(topics, input_word_count)

    # Auto-recommend COMPREHENSIVE for large topic counts (25+ topics)
    # Exclude Extended mode since it's now unlimited like Comprehensive
    auto_recommend_comprehensive = (
        len(topics) >= 25 and
        job.target_length != PodcastLength.COMPREHENSIVE and
        job.target_length != PodcastLength.EXTENDED
    )
    if auto_recommend_comprehensive:
        logger.info(f"Auto-recommending COMPREHENSIVE mode for {len(topics)} topics")

    # Determine if length selection is adequate
    length_warning = None
    recommended_length = None

    # Handle COMPREHENSIVE mode (no word target) vs standard modes
    if word_target is None:
        # Unlimited modes (COMPREHENSIVE or Extended) - no coverage ratio needed, always adequate
        coverage_ratio = float('inf')  # Infinite coverage capacity
        logger.info(f"Unlimited mode ({length_name}) selected - will cover all content regardless of estimated needs ({estimated_words_needed} words)")
    else:
        # Standard modes - check if target is adequate for content
        coverage_ratio = word_target / max(estimated_words_needed, 1)

    if word_target is not None and coverage_ratio < 0.7:  # Selected length covers less than 70% of estimated needs
        # For large content or many topics, recommend COMPREHENSIVE mode
        if estimated_words_needed > 15000 or len(topics) >= 25:
            recommended_length = "COMPREHENSIVE (Process ALL Content)"
        else:
            # Find recommended length from fixed modes
            for length in [PodcastLength.EXTENDED, PodcastLength.LONG, PodcastLength.MEDIUM, PodcastLength.SHORT]:
                cfg = PodcastLength.get_config(length)
                # Skip modes with unlimited word targets (None)
                if cfg['word_target'] is not None and cfg['word_target'] >= estimated_words_needed * 0.8:
                    recommended_length = cfg['display_name']
                    break

            if not recommended_length:
                recommended_length = "COMPREHENSIVE (Process ALL Content)"

        target_description = "unlimited words" if word_target is None else f"{word_target:,} words"
        length_warning = f"""
⚠️  LENGTH WARNING: Your input contains {len(topics)} distinct topics ({input_word_count} words).
    To cover ALL topics adequately, you need approximately {estimated_words_needed:,} words.
    Your selected length "{length_name}" targets {target_description}.

    RECOMMENDATION: Select "{recommended_length}" to ensure complete coverage.

    If you continue with current length, some topics may be summarized or omitted.

    Detected topics:"""
        for i, topic in enumerate(topics[:8], 1):
            length_warning += f"\n      {i}. {topic[:70]}..."
        if len(topics) > 8:
            length_warning += f"\n      ... and {len(topics) - 8} more topics"

    # Build preview for user review
    preview_lines = [
        f"Detected {len(episodes)} episode(s):",
        f"  - {complete_count} complete (ready for processing)",
        f"  - {incomplete_count} incomplete (will be expanded)",
        f"  - {len(topics)} distinct topics detected",
        f"  - Input: {input_word_count} words → Target: {'No limit (COMPREHENSIVE)' if word_target is None else f'{word_target} words'}",
    ]

    # Add news format notification
    if job.is_news_format:
        preview_lines.extend([
            "",
            f"[NEWS] Perplexity News Threads Format Detected:",
            f"  - {len(job.news_urls)} source URLs extracted for research",
            f"  - {len(topics)} news topics will be covered comprehensively"
        ])

    # Add recommendation for large topic counts
    if auto_recommend_comprehensive:
        preview_lines.extend([
            "",
            f"*** RECOMMENDATION: {len(topics)} topics detected - use COMPREHENSIVE mode ***",
            f"    COMPREHENSIVE mode will cover ALL topics without word limits",
            f"    Estimated output: {estimated_words_needed:,} words (~{estimated_words_needed//150} minutes)",
            f"    Current Extended mode may truncate content to fit {word_target:,} word limit"
        ])

    preview_lines.extend([
        "",
        "Episodes found:"
    ])

    for ep in episodes[:5]:  # Show first 5
        status = "Complete" if ep['is_complete'] else "Needs expansion"
        header = ep.get('header', f"Episode {ep['episode_num']}")[:60]
        preview_lines.append(f"  {ep['episode_num']}. {header}... [{status}]")

    if len(episodes) > 5:
        preview_lines.append(f"  ... and {len(episodes) - 5} more")

    # Add length warning if needed
    if length_warning:
        preview_lines.append("")
        preview_lines.append(length_warning)

    return StageResult(
        stage=Stage.ANALYZE,
        output_preview='\n'.join(preview_lines),
        full_output={
            'episodes': episodes,
            'text': text,
            'topics': topics,
            'estimated_words_needed': estimated_words_needed,
            'length_adequate': coverage_ratio >= 0.7
        },
        metadata={
            'episode_count': len(episodes),
            'incomplete_count': incomplete_count,
            'complete_count': complete_count,
            'topic_count': len(topics),
            'input_word_count': input_word_count,
            'estimated_words_needed': estimated_words_needed,
            'coverage_ratio': round(coverage_ratio, 2),
            'length_warning': length_warning is not None,
            'is_news_format': job.is_news_format,
            'news_url_count': len(job.news_urls) if job.news_urls else 0,
            'auto_recommend_comprehensive': auto_recommend_comprehensive
        }
    )


def run_stage_research(job: Job) -> StageResult:
    """Research stage - parallel multi-agent Perplexity research"""
    episodes = job.episodes
    suggestion = job.user_suggestions.get(Stage.RESEARCH, '')
    length_config = job.get_length_config()
    base_agents = length_config['research_agents']
    research_depth = length_config['research_depth']

    # Dynamic research scaling for unlimited modes
    topic_count = len(job.detected_topics)
    user_source_count = len(job.user_sources)

    if job.target_length in [PodcastLength.EXTENDED, PodcastLength.COMPREHENSIVE]:
        # Scale agents based on content complexity
        topic_bonus = min(topic_count // 5, 8)  # +1 agent per 5 topics, max +8
        user_source_bonus = min(user_source_count // 3, 5)  # +1 agent per 3 user sources, max +5
        num_agents = base_agents + topic_bonus + user_source_bonus

        job.research_scaling_applied = True
        job.total_research_agents = num_agents

        logger.info(
            f"Dynamic research scaling: {base_agents} base + {topic_bonus} topic bonus + {user_source_bonus} source bonus = {num_agents} agents total"
        )
        logger.info(
            f"Scaling factors: {topic_count} topics, {user_source_count} user sources"
        )
    else:
        # Fixed scaling for standard modes
        num_agents = base_agents
        job.total_research_agents = num_agents

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

    preview_lines = [f"Research complete using {num_agents} parallel agents per episode ({research_depth} depth):\n"]

    # Research each episode (in parallel across episodes too)
    episode_pool = GeventPool(size=min(5, len(episodes)))
    research_results = list(episode_pool.imap_unordered(
        lambda ep: research_episode_parallel(ep, suggestion, num_agents, research_depth, job),
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

    # Calculate source breakdown for enhanced preview
    user_source_count = len(job.user_sources) if hasattr(job, 'user_sources') else 0
    researched_source_count = len(unique_citations)
    total_source_count = user_source_count + researched_source_count

    # Enhanced source count information
    if user_source_count > 0:
        source_summary = f"Total: {total_source_count} sources ({user_source_count} user-provided + {researched_source_count} researched)"
    else:
        source_summary = f"Total: {researched_source_count} researched sources"

    preview_lines.insert(1, f"{source_summary} across {total_agents} research queries\n")

    # Prepare citation preview with context about full collection
    preview_citations = unique_citations[:20]
    if len(unique_citations) > 20:
        citation_note = f"Showing preview of {len(preview_citations)} citations (Total: {len(unique_citations)} available)"
    else:
        citation_note = f"All {len(unique_citations)} researched citations shown"

    return StageResult(
        stage=Stage.RESEARCH,
        output_preview='\n'.join(preview_lines),
        full_output={'research_map': research_map},
        citations=preview_citations,
        metadata={
            'total_agents': total_agents,
            'episodes_researched': len(research_map),
            'total_citations': len(unique_citations),
            'user_source_count': user_source_count,
            'total_source_count': total_source_count,
            'citation_preview_note': citation_note
        }
    )


def run_stage_research_with_prioritization(job: Job) -> StageResult:
    """Enhanced research with intelligent news prioritization for large news compilations"""

    suggestion = job.user_suggestions.get(Stage.RESEARCH, '')
    length_config = job.get_length_config()
    research_depth = length_config['research_depth']

    # Step 1: Parse headlines into prioritized tiers
    headlines_data = parse_headlines_from_text(job.text)

    if not headlines_data:
        # Fallback to regular episode-based research
        return run_stage_research(job)

    prioritized_content = prioritize_news_content(headlines_data)

    # Step 2: Allocate research resources by tier
    research_allocation = {
        'tier_1_full': {
            'agent_count': 8,  # Full research depth
            'sources_per_topic': 10,
            'research_depth': 'exhaustive'
        },
        'tier_2_brief': {
            'agent_count': 4,  # Moderate research
            'sources_per_topic': 4,
            'research_depth': 'thorough'
        },
        'tier_3_merge': {
            'agent_count': 2,  # Minimal research for context
            'sources_per_topic': 2,
            'research_depth': 'surface'
        }
    }

    # Step 3: Execute tiered research
    all_research_results = {}
    all_citations = []
    total_agents_used = 0

    preview_lines = ["Intelligent news prioritization research complete:\n"]

    for tier_name, content in prioritized_content.items():
        if not content:
            continue

        allocation = research_allocation[tier_name]
        tier_citations = []

        preview_lines.append(f"\n{tier_name.replace('_', ' ').title()} ({len(content)} headlines):")

        for headline in content:
            # Select efficient source samples for this tier
            if headline.get('source_list'):
                selected_sources = efficient_source_selection(
                    headline['source_list'],
                    allocation['sources_per_topic']
                )
            else:
                selected_sources = []

            # Create research context similar to existing system
            research_context = {
                'topic': headline['title'],
                'category': headline.get('category', 'General'),
                'sources': selected_sources,
                'content': headline.get('content', ''),
                'priority_score': calculate_priority_score(headline)
            }

            # Research with appropriate depth using existing parallel research function
            try:
                # Use the existing research_episode_parallel but with adapted data
                fake_episode = {
                    'topic': headline['title'],
                    'content': headline.get('content', ''),
                    'episode_num': len(all_research_results) + 1
                }

                research_result = research_episode_parallel(
                    fake_episode,
                    suggestion,
                    allocation['agent_count'],
                    allocation['research_depth'],
                    job
                )

                all_research_results[headline['title']] = research_result

                # Extract citations from research result
                if 'citations' in research_result:
                    tier_citations.extend(research_result['citations'])
                    all_citations.extend(research_result['citations'])

                # Add to preview
                preview_lines.append(f"  • {headline['title'][:80]}... [{allocation['agent_count']} agents]")

                total_agents_used += allocation['agent_count']

            except Exception as e:
                logger.error(f"Research failed for headline '{headline['title'][:50]}...': {str(e)}")
                preview_lines.append(f"  • {headline['title'][:80]}... [FAILED]")

        # Add tier summary
        preview_lines.append(f"    {len(tier_citations)} citations collected")

    # Step 4: Calculate enhanced source breakdown
    user_source_count = len(job.user_sources) if hasattr(job, 'user_sources') else 0
    researched_source_count = len(all_citations)
    total_source_count = user_source_count + researched_source_count

    # Enhanced source count information
    if user_source_count > 0:
        source_summary = f"Total: {total_source_count} sources ({user_source_count} user-provided + {researched_source_count} researched)"
    else:
        source_summary = f"Total: {researched_source_count} researched sources"

    preview_lines.insert(1, f"{source_summary} across {total_agents_used} research queries\n")

    # Step 5: Citation preview management
    unique_citations = list(dict.fromkeys(all_citations))
    preview_citations = unique_citations[:20]

    if len(unique_citations) > 20:
        citation_note = f"Showing preview of {len(preview_citations)} citations (Total: {len(unique_citations)} available)"
    else:
        citation_note = f"All {len(unique_citations)} researched citations shown"

    # Set enhanced metadata for tracking
    job.research_scaling_applied = True
    job.total_research_agents = total_agents_used

    logger.info(f"Intelligent news research complete: {len(headlines_data)} headlines processed with {total_agents_used} agents")

    return StageResult(
        stage=Stage.RESEARCH,
        output_preview='\n'.join(preview_lines),
        full_output={'hierarchical_research': all_research_results, 'prioritized_content': prioritized_content},
        citations=preview_citations,
        metadata={
            'total_agents': total_agents_used,
            'total_headlines': len(headlines_data),
            'tier_1_count': len(prioritized_content['tier_1_full']),
            'tier_2_count': len(prioritized_content['tier_2_brief']),
            'tier_3_count': len(prioritized_content['tier_3_merge']),
            'total_citations': len(unique_citations),
            'user_source_count': user_source_count,
            'total_source_count': total_source_count,
            'citation_preview_note': citation_note,
            'efficiency_ratio': f"{len(headlines_data)} headlines → {total_agents_used} agents",
            'processing_mode': 'intelligent_news_prioritization'
        }
    )


def _expand_single_episode(episode_data):
    """
    Enterprise-grade single episode expansion function for parallel processing.
    Target: Enable 5x speedup through GeventPool parallelization.

    Args:
        episode_data: Tuple of (episode_dict, research_context, style_guidance)

    Returns:
        Tuple of (ep_num, expanded_text, success, preview_info)
    """
    try:
        ep, research_context, style_guidance = episode_data
        ep_num = ep['episode_num']

        # Build complete research context with guidance
        full_research_context = research_context + style_guidance

        # Call OpenAI GPT-4o for expansion
        expanded = expand_script_with_ai(
            ep['text'],
            context="",
            speakers=['ALEX', 'SARAH'],
            research_context=full_research_context
        )

        # Generate preview info for UI feedback
        preview_info = {
            'message': f"Episode {ep_num}: Expanded to {len(expanded)} chars",
            'preview_lines': []
        }

        lines = expanded.split('\n')[:3]
        for line in lines:
            if line.strip():
                preview_info['preview_lines'].append(f"  {line[:80]}...")

        return (ep_num, expanded, True, preview_info)

    except Exception as e:
        logger.error(f"Enterprise expansion failed for episode {ep_num}: {e}")
        return (ep_num, ep['text'], False, {
            'message': f"Episode {ep_num}: Expansion failed, using original",
            'preview_lines': []
        })

def run_stage_expand(job: Job) -> StageResult:
    """
    Enterprise-grade EXPAND stage with intelligent multi-agent parallelization.

    PERFORMANCE TARGET: 150s → 30s (5x speedup)
    ARCHITECTURE: Sequential episode processing → GeventPool parallel processing
    AGENTS: Dynamic allocation (1 per episode, max 10 concurrent GPT-4o agents)
    """
    episodes = job.episodes
    research_map = job.research_map
    suggestion = job.user_suggestions.get(Stage.EXPAND, '')
    length_config = job.get_length_config()
    word_target = length_config['word_target']
    expand_instruction = length_config['expand_instruction']

    incomplete = [ep for ep in episodes if not ep['is_complete']]

    if not incomplete:
        # All complete, skip expansion
        final_text = '\n\n'.join(ep['text'] for ep in episodes)
        job.final_text = final_text
        return StageResult(
            stage=Stage.EXPAND,
            output_preview="All episodes already complete. No expansion needed.",
            full_output={'expanded_text': final_text},
            metadata={'expanded_count': 0, 'agents_used': 0, 'speedup_achieved': 1.0}
        )

    # ENTERPRISE AGENT ALLOCATION: Dynamic scaling with Render safety limits
    agent_count = min(len(incomplete), SAFE_EXPAND_AGENTS)  # Safe mode: max 3 agents on Render

    word_target_display = "unlimited words" if word_target is None else f"{word_target} words"
    preview_lines = [f"🚀 ENTERPRISE EXPAND: {len(incomplete)} episodes → {agent_count} parallel agents (target: ~{word_target_display}):\n"]

    logger.info(f"Enterprise EXPAND: Processing {len(incomplete)} episodes with {agent_count} parallel GPT-4o agents")

    # Prepare expansion tasks for parallel processing
    expansion_tasks = []
    for ep in incomplete:
        ep_num = ep['episode_num']
        research_context = research_map.get(ep_num, '')

        # Build style guidance for each episode
        if word_target is None:
            style_guidance = f"\n\nLENGTH GUIDANCE: {expand_instruction}"
        else:
            style_guidance = f"\n\nLENGTH GUIDANCE: {expand_instruction} Target approximately {word_target} words total."
        if suggestion:
            style_guidance += f"\n\nUSER GUIDANCE: {suggestion}"

        expansion_tasks.append((ep, research_context, style_guidance))

    # PARALLEL EXECUTION: Process all episodes concurrently with Render safety
    start_time = time.time()

    # Add rate limiting delay to prevent resource exhaustion
    if RENDER_SAFE_MODE:
        time.sleep(SAFE_RATE_LIMIT_DELAY)
        logger.info(f"🛡️ Safe mode: Added {SAFE_RATE_LIMIT_DELAY}s delay before expansion")

    expansion_pool = GeventPool(size=agent_count)
    parallel_results = list(expansion_pool.imap_unordered(_expand_single_episode, expansion_tasks))

    end_time = time.time()
    processing_time = end_time - start_time

    # Process results and build preview
    expanded_map = {}
    success_count = 0

    for ep_num, expanded_text, success, preview_info in parallel_results:
        expanded_map[ep_num] = expanded_text
        if success:
            success_count += 1

        preview_lines.append(preview_info['message'])
        preview_lines.extend(preview_info['preview_lines'])
        if preview_info['preview_lines']:
            preview_lines.append("")

    # Calculate enterprise metrics
    baseline_estimate = len(incomplete) * 30  # 30s per episode estimate
    speedup_achieved = baseline_estimate / max(processing_time, 1)
    success_rate = success_count / len(incomplete)

    logger.info(f"Enterprise EXPAND completed: {processing_time:.1f}s, {speedup_achieved:.1f}x speedup, {success_rate:.1%} success rate")

    # Rebuild full text maintaining episode order
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

    # Enterprise performance metrics for monitoring dashboard
    preview_lines.append(f"\n⚡ ENTERPRISE METRICS:")
    preview_lines.append(f"  • Processing time: {processing_time:.1f}s")
    preview_lines.append(f"  • Speedup achieved: {speedup_achieved:.1f}x")
    preview_lines.append(f"  • Success rate: {success_rate:.1%}")
    preview_lines.append(f"  • Agents utilized: {agent_count}/{len(incomplete)} episodes")

    return StageResult(
        stage=Stage.EXPAND,
        output_preview='\n'.join(preview_lines),
        full_output={'expanded_text': final_text, 'expanded_map': expanded_map},
        metadata={
            'expanded_count': len(expanded_map),
            'agents_used': agent_count,
            'processing_time': processing_time,
            'speedup_achieved': speedup_achieved,
            'success_rate': success_rate,
            'enterprise_mode': True,
            'parallel_episodes': len(incomplete)
        }
    )


def run_stage_enhance(job: Job) -> StageResult:
    """Enhancement stage - Claude polishes AND EXPANDS dialogue to meet length targets"""
    text = job.final_text if job.final_text else job.text
    episodes = parse_episodes(text)
    research_map = job.research_map
    suggestion = job.user_suggestions.get(Stage.ENHANCE, '')
    length_config = job.get_length_config()
    enhance_instruction = length_config['enhance_instruction']
    detail_level = length_config['detail_level']
    word_target = length_config['word_target']

    # Calculate current word count to determine expansion needed
    current_word_count = len(text.split())

    # Handle COMPREHENSIVE mode (no word limit) vs standard modes
    if word_target is None:
        # COMPREHENSIVE mode - content determines length
        is_comprehensive_mode = True
        words_needed = 0  # No artificial limit
        expansion_ratio = 999  # Large number indicating comprehensive expansion (avoids float('inf') formatting issues)
        logger.info(f"Enhance stage: COMPREHENSIVE mode, current={current_word_count} words, no target limit")

        preview_lines = [f"Claude enhancement (COMPREHENSIVE mode - no word limit):\n"]
        preview_lines.append(f"Input: {current_word_count} words → Processing ALL content with full detail\n")

        # Estimate content-appropriate length (3-5 minutes per major topic)
        num_topics = len(job.detected_topics) if job.detected_topics else 1
        estimated_minutes_per_topic = 4  # Average 4 minutes per topic
        estimated_total_minutes = num_topics * estimated_minutes_per_topic
        estimated_words = estimated_total_minutes * 150  # ~150 words per minute
        words_per_episode = estimated_words // max(len(episodes), 1)

        logger.info(f"COMPREHENSIVE: Estimated {estimated_total_minutes} min ({estimated_words} words) for {num_topics} topics")

    else:
        # Standard mode with word targets
        is_comprehensive_mode = False
        words_needed = max(0, word_target - current_word_count)
        expansion_ratio = word_target / max(current_word_count, 1)

        logger.info(f"Enhance stage: current={current_word_count} words, target={word_target}, ratio={expansion_ratio:.2f}x")

        preview_lines = [f"Claude enhancement ({detail_level} detail, ~{word_target} words target):\n"]
        preview_lines.append(f"Input: {current_word_count} words → Target: {word_target} words ({expansion_ratio:.1f}x expansion)\n")

        # Calculate per-episode word targets
        num_episodes = len(episodes)
        words_per_episode = word_target // max(num_episodes, 1)

    if not CLAUDE_API_KEY:
        return StageResult(
            stage=Stage.ENHANCE,
            output_preview="Claude API not configured. Skipping enhancement.",
            full_output={'enhanced_text': text},
            metadata={'enhanced': False}
        )

    all_changes = []
    enhanced_map = {}

    # Get detected topics for mandatory coverage
    if job.detected_topics:
        all_topics = job.detected_topics
    else:
        all_topics, _ = detect_topics(text)  # Extract topics, ignore metadata in fallback
    topic_list = "\n".join(f"   - {t[:80]}" for t in all_topics)

    # ENTERPRISE AGENT ALLOCATION: Dynamic scaling with Render safety limits
    agent_count = min(len(episodes), SAFE_ENHANCE_AGENTS)  # Safe mode: max 2 agents on Render

    logger.info(f"Enterprise ENHANCE: Processing {len(episodes)} episodes with {agent_count} parallel Claude agents")

    # Prepare enhancement tasks for parallel processing
    enhancement_tasks = []
    for ep in episodes:
        ep_num = ep['episode_num']
        research = research_map.get(ep_num, '')
        ep_word_count = len(ep['text'].split())

        # Build MANDATORY TOPIC COVERAGE + expansion instructions
        mandatory_coverage = f"""
=== MANDATORY COMPLETE COVERAGE - READ FIRST ===
You MUST cover ALL {len(all_topics)} of the following topics in the dialogue.
DO NOT SKIP ANY TOPIC. EVERY topic below MUST appear in your output:

{topic_list}

FAILURE TO COVER ANY TOPIC IS UNACCEPTABLE.
After writing, verify EACH topic above has corresponding dialogue content.
=== END MANDATORY COVERAGE ===
"""
        if is_comprehensive_mode:
            # COMPREHENSIVE mode - no word limits, content-driven expansion
            style_guidance = f"""{mandatory_coverage}

COMPREHENSIVE CONTENT EXPANSION - NO WORD LIMITS:
You are in COMPREHENSIVE mode. There are NO word or time constraints.
This episode should be approximately {words_per_episode} words (currently {ep_word_count} words) based on content depth.
Your goal is to create thorough, educational dialogue that covers EVERY topic with full detail:

1. Spend 3-5 minutes of dialogue (450-750 words) on EACH major topic
2. Include multiple detailed examples, case studies, and real-world scenarios for EVERY point
3. Have ALEX ask probing follow-up questions that SARAH answers comprehensively
4. Add extensive educational context, background information, and expert perspectives
5. Explore implications, applications, and future trends for each topic
6. Include analogies, "think of it like" explanations, and conversational depth
7. Add relevant tangents and interesting connections between topics
8. Ensure every piece of information from the source material appears in the dialogue

LENGTH IS DETERMINED BY CONTENT RICHNESS, NOT ARBITRARY LIMITS.
Generate as much dialogue as needed to thoroughly cover all topics.

{enhance_instruction}"""
        elif expansion_ratio > 1.5:
            # Need significant expansion for standard modes
            style_guidance = f"""{mandatory_coverage}

EXPANSION REQUIREMENT:
The current content is {current_word_count} words but MUST be expanded to approximately {word_target} words.
This episode should be approximately {words_per_episode} words (currently {ep_word_count} words).
You MUST {expansion_ratio:.1f}x expand the content by:
1. Adding multiple detailed examples for EVERY point mentioned
2. Including relevant analogies and real-world scenarios
3. Having ALEX ask follow-up questions that SARAH answers in depth
4. Adding educational context and background information
5. Expanding on implications and applications of each topic
6. Including "for example" and "think of it like" explanations throughout

DO NOT just polish - you MUST significantly EXPAND the dialogue while covering ALL topics listed above.

{enhance_instruction}"""
        else:
            # Minor expansion or just polish - still require ALL topics covered
            style_guidance = f"""{mandatory_coverage}

LENGTH/DETAIL GUIDANCE: {enhance_instruction}
Target approximately {words_per_episode} words for this episode.
Remember: ALL topics listed above MUST be covered."""

        if suggestion:
            style_guidance += f"\n\nADDITIONAL USER GUIDANCE: {suggestion}"

        # Build episode data for parallel processing
        enhancement_tasks.append({
            'episode_num': ep_num,
            'text': ep['text'],
            'research': research,
            'speakers': ['ALEX', 'SARAH'],
            'style_guidance': style_guidance,
            'job': job
        })

    # PARALLEL EXECUTION: Process all episodes concurrently with quality assurance and Render safety
    start_time = time.time()

    # Add rate limiting delay to prevent resource exhaustion
    if RENDER_SAFE_MODE:
        time.sleep(SAFE_RATE_LIMIT_DELAY)
        logger.info(f"🛡️ Safe mode: Added {SAFE_RATE_LIMIT_DELAY}s delay before enhancement")

    enhancement_pool = GeventPool(size=agent_count)
    parallel_results = list(enhancement_pool.imap_unordered(enhance_episode_with_claude, enhancement_tasks))

    end_time = time.time()
    processing_time = end_time - start_time

    # Process results and build maps
    enhanced_map = {}
    success_count = 0

    for result in parallel_results:
        ep_num = result.get('episode_num', 0)
        enhanced_map[ep_num] = result['enhanced_text']

        if result['success']:
            success_count += 1

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

    # Calculate enterprise metrics
    baseline_estimate = len(episodes) * 45  # 45s per episode estimate for Claude API
    speedup_achieved = baseline_estimate / max(processing_time, 1)
    success_rate = success_count / len(episodes)

    logger.info(f"Enterprise ENHANCE completed: {processing_time:.1f}s, {speedup_achieved:.1f}x speedup, {success_rate:.1%} success rate")

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

    # Verify topic coverage - CRITICAL for ensuring no content is missed
    coverage_result = verify_topic_coverage(final_text, all_topics)
    final_word_count = len(final_text.split())

    preview_lines.append(f"\n📊 Coverage Analysis: {final_word_count} words generated")
    preview_lines.append(f"   Topics covered: {len(coverage_result['covered'])}/{len(all_topics)} ({coverage_result['coverage_rate']*100:.0f}%)")

    if not coverage_result['fully_covered']:
        missing_list = "\n".join(f"      ❌ {t[:70]}" for t in coverage_result['missing'][:8])
        preview_lines.append(f"\n⚠️  WARNING: {len(coverage_result['missing'])} topics may not be adequately covered:")
        preview_lines.append(missing_list)
        if len(coverage_result['missing']) > 8:
            preview_lines.append(f"      ... and {len(coverage_result['missing']) - 8} more")
        preview_lines.append("\n💡 SUGGESTION: Add a note in the suggestion box to ensure these topics are included,")
        preview_lines.append("   or select a longer podcast length to allow more comprehensive coverage.")

    # Enterprise performance metrics for monitoring dashboard
    preview_lines.append(f"\n⚡ ENTERPRISE METRICS:")
    preview_lines.append(f"  • Processing time: {processing_time:.1f}s")
    preview_lines.append(f"  • Speedup achieved: {speedup_achieved:.1f}x")
    preview_lines.append(f"  • Success rate: {success_rate:.1%}")
    preview_lines.append(f"  • Agents utilized: {agent_count}/{len(episodes)} episodes")
    preview_lines.append(f"  • Quality assurance: {coverage_result['coverage_rate']:.1%} topic coverage")

    return StageResult(
        stage=Stage.ENHANCE,
        output_preview='\n'.join(preview_lines),
        full_output={'enhanced_text': final_text, 'coverage': coverage_result},
        changes=all_changes,
        metadata={
            'total_words_added': total_added,
            'episodes_enhanced': len(all_changes),
            'final_word_count': final_word_count,
            'topics_covered': len(coverage_result['covered']),
            'topics_missing': len(coverage_result['missing']),
            'coverage_rate': coverage_result['coverage_rate'],
            'agents_used': agent_count,
            'processing_time': processing_time,
            'speedup_achieved': speedup_achieved,
            'success_rate': success_rate,
            'enterprise_mode': True,
            'parallel_episodes': len(episodes)
        }
    )


def run_stage_generate(job: Job) -> StageResult:
    """Generate stage - TTS audio generation"""
    text = job.final_text if job.final_text else job.text
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
        chunk_dicts = split_by_speaker(processed, voice_assignments)
        chunks = [(d['text'], d['voice'], d.get('speaker')) for d in chunk_dicts]
    else:
        chunks = split_into_chunks(processed)
        chunks = [(chunk, job.voice, None) for chunk in chunks]

    total_chunks = len(chunks)

    # EMERGENCY: Hard cap to prevent OpenAI credit burn
    if total_chunks > 50:
        logger.error(f"EMERGENCY: Job {job_id} generating {total_chunks} chunks, aborting to prevent credit burn")
        # Raise exception to mark job as ERROR and prevent pipeline advancement
        raise ValueError(
            f"Safety limit exceeded: {total_chunks} chunks detected (max: 50). "
            f"Normal podcasts generate ~20 chunks. "
            f"Estimated cost would have been: ${total_chunks * 0.30:.2f}. "
            f"Please use shorter podcast length or reduce content complexity."
        )

    # ENTERPRISE TTS GENERATION: Apply existing pool logic to interactive mode
    # Target: 250s → 30s (8x speedup through intelligent agent scaling)

    def _generate_single_chunk_enterprise(args):
        """
        Enterprise-grade single chunk TTS generation for parallel processing.
        Adapted from existing legacy mode implementation with enhanced error handling.
        """
        try:
            idx, chunk_text, chunk_voice, chunk_speaker = args
            output_path = job_dir / f"chunk_{idx:04d}.mp3"

            # Enterprise rate limiting to prevent OpenAI 429 errors
            rate_limiter.wait_if_needed()

            # Generate TTS with enterprise retry logic
            response = call_openai_tts_with_retry(client, job.model, chunk_voice, chunk_text)
            response.stream_to_file(str(output_path))

            # Validate file generation
            if output_path.exists() and output_path.stat().st_size > 0:
                return (idx, output_path, chunk_voice, chunk_speaker, None)
            else:
                return (idx, None, chunk_voice, chunk_speaker, "Empty file generated")

        except Exception as e:
            logger.error(f"Enterprise TTS chunk {idx} failed: {e}")
            return (idx, None, chunk_voice, chunk_speaker, str(e))

    # DYNAMIC AGENT ALLOCATION: Intelligent scaling with Render safety limits
    # Scale based on chunk count with Render hosting constraints
    max_concurrent = min(total_chunks, SAFE_TTS_AGENTS)  # Safe mode: max 8 agents on Render
    agent_count = max_concurrent

    preview_lines = [f"🚀 ENTERPRISE GENERATE: {total_chunks} chunks → {agent_count} parallel TTS agents:\n"]

    logger.info(f"Enterprise GENERATE: Processing {total_chunks} chunks with {agent_count} parallel OpenAI TTS agents")

    # Prepare chunk arguments for parallel processing
    chunk_args = [(i, chunk_text, chunk_voice, speaker) for i, (chunk_text, chunk_voice, speaker) in enumerate(chunks)]

    # PARALLEL EXECUTION: Process all chunks concurrently with Render safety
    start_time = time.time()

    # Add rate limiting delay to prevent resource exhaustion
    if RENDER_SAFE_MODE:
        time.sleep(SAFE_RATE_LIMIT_DELAY)
        logger.info(f"🛡️ Safe mode: Added {SAFE_RATE_LIMIT_DELAY}s delay before TTS generation")

    client = get_client()
    tts_pool = GeventPool(size=agent_count)
    parallel_results = list(tts_pool.imap_unordered(_generate_single_chunk_enterprise, chunk_args))

    end_time = time.time()
    processing_time = end_time - start_time

    # Process results and collect successful chunks
    chunk_files = []
    results_map = {}
    success_count = 0
    error_count = 0

    for idx, chunk_path, chunk_voice, chunk_speaker, error in parallel_results:
        if error is None and chunk_path:
            chunk_files.append(chunk_path)
            results_map[idx] = chunk_path
            success_count += 1
        else:
            error_count += 1
            logger.error(f"TTS chunk {idx} failed: {error}")

    # Sort chunk files by index for proper concatenation order
    sorted_chunk_files = [results_map[i] for i in sorted(results_map.keys())]

    # Calculate enterprise metrics
    baseline_estimate = total_chunks * 5  # 5s per chunk estimate (sequential)
    speedup_achieved = baseline_estimate / max(processing_time, 1)
    success_rate = success_count / total_chunks
    estimated_cost = total_chunks * 0.30  # ~$0.30 per chunk

    logger.info(f"Enterprise GENERATE completed: {processing_time:.1f}s, {speedup_achieved:.1f}x speedup, {success_rate:.1%} success rate")

    preview_lines.append(f"  • {success_count}/{total_chunks} chunks successful")
    preview_lines.append(f"  • {error_count} chunks failed")
    preview_lines.append(f"  • Estimated OpenAI cost: ${estimated_cost:.2f}")

    # Enterprise performance metrics for monitoring dashboard
    preview_lines.append(f"\n⚡ ENTERPRISE METRICS:")
    preview_lines.append(f"  • Processing time: {processing_time:.1f}s")
    preview_lines.append(f"  • Speedup achieved: {speedup_achieved:.1f}x")
    preview_lines.append(f"  • Success rate: {success_rate:.1%}")
    preview_lines.append(f"  • Agents utilized: {agent_count} TTS agents")
    preview_lines.append(f"  • Cost efficiency: ${estimated_cost/max(processing_time/60, 1):.2f}/min")

    # Use sorted chunk files for concatenation to maintain episode order
    chunk_files = sorted_chunk_files

    if job.multi_voice:
        preview_lines.append(f"  - Multi-voice: ALEX (echo) + SARAH (shimmer)")

    return StageResult(
        stage=Stage.GENERATE,
        output_preview='\n'.join(preview_lines),
        full_output={'chunk_files': chunk_files, 'total_chunks': total_chunks},
        metadata={
            'total_chunks': total_chunks,
            'successful_chunks': len(chunk_files),
            'failed_chunks': error_count,
            'agents_used': agent_count,
            'processing_time': processing_time,
            'speedup_achieved': speedup_achieved,
            'success_rate': success_rate,
            'estimated_cost': estimated_cost,
            'enterprise_mode': True,
            'parallel_tts_agents': agent_count
        }
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

    # Defensive check: full_output might be None if generation was aborted
    if generate_result.full_output is None:
        logger.warning(f"Job {job_id}: GENERATE stage full_output is None, cannot combine")
        return StageResult(
            stage=Stage.COMBINE,
            output_preview="❌ Audio generation was halted before completion.\n\nNo audio chunks available to combine.\n\nPlease review the generation stage output for details.",
            full_output={},
            metadata={'success': False, 'reason': 'generation_aborted'}
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

    # ENTERPRISE PERFORMANCE MONITORING: Track stage execution timing
    stage_start_time = time.time()

    try:
        # Run appropriate stage processor with performance tracking
        if stage == Stage.ANALYZE:
            result = run_stage_analyze(job)
        elif stage == Stage.RESEARCH:
            # Check if content is a news compilation that benefits from intelligent prioritization
            if detect_news_compilation_format(job.text):
                logger.info(f"Job {job_id}: Using intelligent news prioritization for large compilation")
                result = run_stage_research_with_prioritization(job)
            else:
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

        stage_end_time = time.time()

        # ENTERPRISE METRICS: Track successful stage completion
        performance_monitor.track_stage_metrics(
            job=job,
            stage=stage,
            start_time=stage_start_time,
            end_time=stage_end_time,
            metadata=result.metadata if result.metadata else {}
        )

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
        stage_end_time = time.time()

        # ENTERPRISE METRICS: Track failed stage execution
        error_metadata = {
            'error': str(e),
            'success_rate': 0.0,
            'enterprise_mode': False
        }
        performance_monitor.track_stage_metrics(
            job=job,
            stage=stage,
            start_time=stage_start_time,
            end_time=stage_end_time,
            metadata=error_metadata
        )

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

    # Parse target length
    length_str = request.form.get('target_length', 'medium').lower()
    try:
        target_length = PodcastLength(length_str)
    except ValueError:
        target_length = PodcastLength.MEDIUM

    # Parse target mode
    mode_str = request.form.get('target_mode', 'educational').lower()
    try:
        target_mode = PodcastMode(mode_str)
    except ValueError:
        target_mode = PodcastMode.EDUCATIONAL

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
        auto_expand=request.form.get('auto_expand', 'true').lower() == 'true',
        target_length=target_length,
        target_mode=target_mode
    )

    length_config = job.get_length_config()
    logger.info(f"Job {job.id}: Target length={target_length.value}, research_agents={length_config['research_agents']}")

    job_store.create_job(job)
    logger.info(f"Job {job.id}: Created interactive pipeline job")

    # Start first stage in background
    gevent.spawn(run_job_stage, job.id, Stage.ANALYZE)

    return jsonify({
        'job_id': job.id,
        'status': 'created',
        'message': 'Pipeline started. Poll /api/job/{job_id}/status for updates.'
    })


def calculate_job_progress(job: Job) -> dict:
    """
    Calculate real-time progress for enterprise job processing.

    Returns accurate progress percentage and stage details for UI progress bars.
    """
    stage_weights = {
        Stage.ANALYZE: 10,   # 10% - Fast analysis
        Stage.RESEARCH: 25,  # 25% - Research with multiple agents
        Stage.EXPAND: 20,    # 20% - Enterprise parallel expansion
        Stage.ENHANCE: 25,   # 25% - Enterprise parallel enhancement
        Stage.GENERATE: 15,  # 15% - Enterprise parallel TTS
        Stage.COMBINE: 5     # 5% - Quick audio assembly
    }

    completed_stages = list(job.stage_results.keys())
    current_stage = job.current_stage

    # Calculate base progress from completed stages
    base_progress = sum(stage_weights[stage] for stage in completed_stages)

    # Add partial progress for current stage if running
    current_stage_progress = 0
    if current_stage and job.status == JobStatus.RUNNING:
        # Estimate 50% completion if currently processing
        current_stage_progress = stage_weights[current_stage] * 0.5
    elif current_stage and job.status == JobStatus.PAUSED_FOR_REVIEW:
        # 100% completion if paused for review (stage done)
        if current_stage not in completed_stages:
            current_stage_progress = stage_weights[current_stage]

    total_progress = min(base_progress + current_stage_progress, 100)

    # Get enterprise metrics if available
    enterprise_info = {}
    if current_stage and current_stage in job.stage_results:
        metadata = job.stage_results[current_stage].metadata
        if metadata and metadata.get('enterprise_mode'):
            enterprise_info = {
                'agents_used': metadata.get('agents_used', 1),
                'speedup_achieved': metadata.get('speedup_achieved', 1.0),
                'success_rate': metadata.get('success_rate', 1.0),
                'safe_mode_active': RENDER_SAFE_MODE
            }

    return {
        'percentage': round(total_progress, 1),
        'stage_name': Stage.get_display_name(current_stage) if current_stage else 'Initializing',
        'completed_stages': len(completed_stages),
        'total_stages': 6,
        'status_text': f"{Stage.get_display_name(current_stage)} ({total_progress:.0f}%)" if current_stage else "Starting...",
        'enterprise_metrics': enterprise_info,
        'estimated_time_remaining': estimate_remaining_time(job, total_progress)
    }


def estimate_remaining_time(job: Job, current_progress: float) -> str:
    """Estimate remaining processing time based on progress and enterprise speedups"""
    if current_progress >= 100:
        return "Complete"
    elif current_progress <= 0:
        return "Starting..."

    # Base time estimates with enterprise speedups
    if RENDER_SAFE_MODE:
        # Conservative estimates for safe mode
        base_time_minutes = 4  # 4 minutes total in safe mode
    else:
        # Full enterprise mode estimates
        base_time_minutes = 2.5  # 2.5 minutes total in enterprise mode

    remaining_progress = (100 - current_progress) / 100
    remaining_minutes = base_time_minutes * remaining_progress

    if remaining_minutes < 1:
        return f"{int(remaining_minutes * 60)}s"
    else:
        return f"{remaining_minutes:.1f}m"


@app.route('/api/job/<job_id>/status')
@login_required
@limiter.exempt  # Exempt from rate limiting - polling endpoint
def get_interactive_job_status(job_id):
    """Get current job status and stage preview if paused"""
    job_id = re.sub(r'[^a-zA-Z0-9_-]', '', job_id)
    job = job_store.get_job(job_id)

    if not job:
        return jsonify({'error': 'Job not found'}), 404

    # Calculate real-time progress percentage
    stage_progress = calculate_job_progress(job)

    response = {
        'job_id': job.id,
        'status': job.status.value,
        'current_stage': job.current_stage.value if job.current_stage else None,
        'current_stage_name': Stage.get_display_name(job.current_stage) if job.current_stage else None,
        'awaiting_input': job.status == JobStatus.PAUSED_FOR_REVIEW,
        'progress': stage_progress  # Real-time progress tracking
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


@app.route('/api/performance/metrics')
@login_required
def get_performance_metrics():
    """
    Enterprise Performance Monitoring API

    Returns comprehensive performance metrics and optimization recommendations
    for the monitoring dashboard.
    """
    try:
        summary = performance_monitor.get_performance_summary()

        # Add current status information
        summary['system_status'] = {
            'enterprise_features_enabled': True,
            'parallel_processing_active': True,
            'monitoring_active': True,
            'last_updated': datetime.utcnow().isoformat()
        }

        return jsonify(summary)

    except Exception as e:
        logger.error(f"Performance metrics API error: {e}")
        return jsonify({
            'error': 'Failed to retrieve performance metrics',
            'message': str(e)
        }), 500


@app.route('/api/performance/recommendations')
@login_required
def get_optimization_recommendations():
    """
    Enterprise Optimization Recommendations API

    Returns AI-powered optimization recommendations based on recent performance data.
    """
    try:
        recommendations = performance_monitor.get_optimization_recommendations()

        return jsonify({
            'recommendations': recommendations,
            'count': len(recommendations),
            'generated_at': datetime.utcnow().isoformat()
        })

    except Exception as e:
        logger.error(f"Performance recommendations API error: {e}")
        return jsonify({
            'error': 'Failed to generate recommendations',
            'message': str(e)
        }), 500


if __name__ == '__main__':
    # SECURITY: debug=False in production (use FLASK_DEBUG=1 for local dev)
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug_mode, port=5000)
