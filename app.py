"""
Podcast TTS Web App
Converts podcast scripts to audio using OpenAI TTS API
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
from pathlib import Path
from functools import wraps
from collections import deque
from flask import Flask, render_template, request, jsonify, send_file, Response, session, redirect, url_for

from openai import OpenAI
import httpx
import sys
import gevent
from gevent.pool import Pool as GeventPool

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())

# Configuration
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
APP_PASSWORD = os.environ.get('PASSWORD', '')
TEMP_DIR = Path(tempfile.gettempdir()) / 'podcast-tts'
TEMP_DIR.mkdir(exist_ok=True)

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

# Script expansion prompt for GPT-4o
SCRIPT_EXPANSION_PROMPT = """You are a podcast script writer. Expand the given outline into natural dialogue between the hosts.

FORMAT RULES:
- Use speaker names followed by colon (ALEX:, SARAH:, etc.) - match the names used in the context
- Write natural, conversational dialogue with back-and-forth between hosts
- The first speaker is typically the host/interviewer, the second is the expert/guest
- Match the tone, style, and technical depth of the provided context
- Do NOT include stage directions in parentheses like (laughs) or (pauses)
- Aim for 800-1200 words per episode section
- Include specific examples, numbers, and details from the outline
- Make it educational but engaging - explain concepts clearly

OUTPUT: Return ONLY the expanded dialogue script, no explanations or meta-commentary."""

# Script expansion model
SCRIPT_EXPANSION_MODEL = os.environ.get('SCRIPT_EXPANSION_MODEL', 'gpt-4o')


class RateLimiter:
    """Adaptive rate limiter to prevent OpenAI 429 errors"""
    def __init__(self, max_requests=8, window_seconds=1):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests = deque()

    def wait_if_needed(self):
        now = time.time()
        # Remove old requests outside window
        while self.requests and self.requests[0] < now - self.window:
            self.requests.popleft()

        if len(self.requests) >= self.max_requests:
            sleep_time = self.requests[0] + self.window - now
            if sleep_time > 0:
                gevent.sleep(sleep_time)

        self.requests.append(time.time())


# Global rate limiter instance
rate_limiter = RateLimiter(max_requests=8, window_seconds=1)


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


def get_client():
    """Get OpenAI client with timeout"""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    # Set 120 second timeout for TTS API calls
    return OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=httpx.Timeout(120.0, connect=10.0)
    )


def preprocess_text(text):
    """Clean text for natural TTS reading"""
    # Remove markdown headers
    text = re.sub(r'^#{1,4}\s*', '', text, flags=re.MULTILINE)

    # Remove markdown formatting
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # Bold
    text = re.sub(r'\*([^*]+)\*', r'\1', text)  # Italic
    text = re.sub(r'```[^`]*```', '', text, flags=re.DOTALL)  # Code blocks
    text = re.sub(r'`([^`]+)`', r'\1', text)  # Inline code
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)  # Horizontal rules
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)  # Bullet points
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)  # Numbered lists

    # Clean special characters
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(''', "'").replace(''', "'")
    text = text.replace('—', ' - ')
    text = text.replace('→', 'to')

    # Clean up whitespace
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
    # 1. No dialogue AND has outline markers, OR
    # 2. Has parenthetical instructions, OR
    # 3. Very short content with instruction words
    if not has_dialogue and (has_bullets or has_numbered_list or has_paren_instructions):
        return True
    if has_paren_instructions:
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
        return [{'text': text, 'header': '', 'is_complete': True, 'episode_num': 0}]

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


def expand_script_with_ai(outline_text, context="", speakers=None):
    """
    Use GPT-4o to expand an episode outline into full podcast dialogue.

    Args:
        outline_text: The incomplete episode outline
        context: Text from previous complete episodes for style matching
        speakers: List of speaker names to use (e.g., ['ALEX', 'SARAH'])

    Returns:
        Expanded dialogue script
    """
    client = get_client()

    # Build the user prompt
    speaker_info = ""
    if speakers:
        speaker_info = f"\nSpeakers to use: {', '.join(speakers)}"

    context_snippet = ""
    if context:
        # Limit context to last ~2000 chars to avoid token limits
        context_snippet = f"\n\nStyle reference from previous episodes:\n{context[-2000:]}"

    user_prompt = f"""Expand this episode outline into a full podcast dialogue script:{speaker_info}{context_snippet}

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
        print(f"[EXPAND] Error expanding script: {e}", file=sys.stderr, flush=True)
        raise


def auto_expand_script(text, progress_callback=None):
    """
    Automatically detect and expand incomplete episodes in a script.

    Args:
        text: Full script text
        progress_callback: Optional function to call with progress updates

    Returns:
        Tuple of (expanded_text, expansion_count)
    """
    episodes = parse_episodes(text)

    if not episodes or all(ep['is_complete'] for ep in episodes):
        return text, 0

    # Find incomplete episodes
    incomplete = [ep for ep in episodes if not ep['is_complete']]
    complete = [ep for ep in episodes if ep['is_complete']]

    if not incomplete:
        return text, 0

    # Extract speakers from complete episodes for consistency
    speakers = detect_speakers('\n'.join(ep['text'] for ep in complete))
    if not speakers:
        speakers = ['ALEX', 'SARAH']  # Default speakers

    # Build context from complete episodes
    context = '\n\n'.join(ep['text'] for ep in complete[:3])  # Use first 3 complete episodes

    # Expand each incomplete episode
    expanded_episodes = {}
    for i, ep in enumerate(incomplete):
        if progress_callback:
            progress_callback(f"Expanding Episode {ep['episode_num']} with AI... ({i+1}/{len(incomplete)})")

        print(f"[EXPAND] Expanding Episode {ep['episode_num']}...", file=sys.stderr, flush=True)

        try:
            expanded_text = expand_script_with_ai(ep['text'], context, speakers)
            expanded_episodes[ep['episode_num']] = expanded_text
            print(f"[EXPAND] Episode {ep['episode_num']} expanded ({len(expanded_text)} chars)", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[EXPAND] Failed to expand Episode {ep['episode_num']}: {e}", file=sys.stderr, flush=True)
            # Keep original text on failure
            expanded_episodes[ep['episode_num']] = ep['text']

    # Rebuild the full script with expanded episodes
    result_parts = []
    for ep in episodes:
        if ep['episode_num'] in expanded_episodes:
            result_parts.append(expanded_episodes[ep['episode_num']])
        else:
            result_parts.append(ep['text'])

    return '\n\n'.join(result_parts), len(incomplete)


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if not APP_PASSWORD:
        return redirect(url_for('index'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == APP_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('index'))
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
def generate():
    """Generate audio from text - streams progress via SSE"""

    # Extract request data BEFORE creating generator (to avoid request context issues)
    if 'file' in request.files and request.files['file'].filename:
        file = request.files['file']
        text = file.read().decode('utf-8')
    else:
        text = request.form.get('text', '')

    voice = request.form.get('voice', 'nova')
    model = request.form.get('model', 'tts-1-hd')
    multi_voice = request.form.get('multi_voice', 'false').lower() == 'true'
    auto_expand = request.form.get('auto_expand', 'true').lower() == 'true'  # Default ON

    def generate_stream(text, voice, model, multi_voice, auto_expand):
        job_id = str(uuid.uuid4())[:8]
        job_dir = TEMP_DIR / job_id
        job_dir.mkdir(exist_ok=True)

        try:
            if not text.strip():
                yield f"data: {json.dumps({'error': 'No text provided'})}\n\n"
                return

            if voice not in VOICES:
                voice = 'nova'
            if model not in MODELS:
                model = 'tts-1-hd'

            # Auto-expand incomplete episodes if enabled
            if auto_expand:
                yield f"data: {{\"status\": \"processing\", \"message\": \"Analyzing script for incomplete sections...\"}}\n\n"

                # Check for incomplete episodes
                episodes = parse_episodes(text)
                incomplete_count = sum(1 for ep in episodes if not ep['is_complete'])

                if incomplete_count > 0:
                    yield f"data: {{\"status\": \"processing\", \"message\": \"Found {incomplete_count} incomplete episode(s). Expanding with AI...\"}}\n\n"
                    print(f"[EXPAND] Found {incomplete_count} incomplete episodes, expanding...", file=sys.stderr, flush=True)

                    try:
                        # Expand incomplete episodes
                        text, expanded_count = auto_expand_script(text)
                        yield f"data: {{\"status\": \"processing\", \"message\": \"Expanded {expanded_count} episode(s). Continuing to audio generation...\"}}\n\n"
                        print(f"[EXPAND] Expansion complete, {expanded_count} episodes expanded", file=sys.stderr, flush=True)
                    except Exception as e:
                        yield f"data: {{\"status\": \"processing\", \"message\": \"Script expansion failed: {str(e)}. Continuing with original text...\"}}\n\n"
                        print(f"[EXPAND] Expansion failed: {e}", file=sys.stderr, flush=True)

            # Preprocess text
            processed = preprocess_text(text)

            # Check for multi-voice mode with speaker detection
            speaker_voices = {}
            if multi_voice:
                # Detect speakers from the original text (before preprocessing removes the markers)
                speakers = detect_speakers(text)
                if speakers:
                    # Build voice mapping for each speaker
                    for speaker in speakers:
                        speaker_voices[speaker] = get_voice_for_speaker(speaker)
                    print(f"[TTS] Multi-voice mode: detected {len(speakers)} speakers: {speaker_voices}", file=sys.stderr, flush=True)

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

            yield f"data: {{\"status\": \"processing\", \"message\": \"Starting generation...\", \"total\": {total_chunks}}}\n\n"
            print(f"[TTS] Starting generation: {total_chunks} chunks, model={model}, mode={mode_desc}", file=sys.stderr, flush=True)

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

            # Use gevent pool for parallel API calls - 1 agent per chunk
            pool_size = MAX_CONCURRENT_CHUNKS if MAX_CONCURRENT_CHUNKS > 0 else total_chunks
            pool = GeventPool(size=pool_size)
            print(f"[TTS] Starting parallel generation with {pool_size} concurrent agents (1 per chunk)", file=sys.stderr, flush=True)

            # Process chunks in parallel
            for result in pool.imap_unordered(generate_single_chunk, chunk_args):
                idx, path, chunk_voice, chunk_speaker, error = result
                completed += 1

                if error:
                    print(f"[TTS] ERROR: Chunk {idx+1} failed: {error}", file=sys.stderr, flush=True)
                    yield f"data: {{\"status\": \"error\", \"message\": \"Chunk {idx+1} failed: {error}\"}}\n\n"
                    return

                results[idx] = path
                speaker_info = f" [{chunk_speaker}:{chunk_voice}]" if chunk_speaker else f" [{chunk_voice}]"
                print(f"[TTS] Chunk {idx+1} done{speaker_info} ({path.stat().st_size} bytes) [{completed}/{total_chunks}]", file=sys.stderr, flush=True)
                yield f"data: {{\"status\": \"processing\", \"message\": \"Completed {completed}/{total_chunks} chunks ({pool_size} agents)\", \"current\": {completed}, \"total\": {total_chunks}}}\n\n"

            # Get chunk files in correct order for concatenation
            chunk_files = [results[i] for i in sorted(results.keys())]

            # Concatenate chunks
            yield f"data: {{\"status\": \"processing\", \"message\": \"Combining audio chunks...\", \"current\": {total_chunks}, \"total\": {total_chunks}}}\n\n"

            output_path = job_dir / "podcast.mp3"
            concatenate_mp3_files(chunk_files, output_path)

            if output_path.exists():
                size_mb = output_path.stat().st_size / (1024 * 1024)
                yield f"data: {{\"status\": \"complete\", \"message\": \"Audio generated successfully!\", \"download_id\": \"{job_id}\", \"size_mb\": {size_mb:.1f}}}\n\n"
            else:
                yield f"data: {{\"status\": \"error\", \"message\": \"Failed to create final audio file\"}}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': f'Error: {str(e)}'})}\n\n"

    response = Response(generate_stream(text, voice, model, multi_voice, auto_expand), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Connection'] = 'keep-alive'
    response.headers['X-Accel-Buffering'] = 'no'  # Critical for Render's nginx proxy
    return response


@app.route('/download/<job_id>')
@login_required
def download(job_id):
    """Download generated audio file"""
    # Sanitize job_id to prevent path traversal
    job_id = re.sub(r'[^a-zA-Z0-9-]', '', job_id)

    output_path = TEMP_DIR / job_id / "podcast.mp3"

    if not output_path.exists():
        return jsonify({'error': 'File not found'}), 404

    return send_file(
        output_path,
        as_attachment=True,
        download_name=f"podcast-{job_id}.mp3",
        mimetype='audio/mpeg'
    )


@app.route('/cleanup/<job_id>', methods=['POST'])
def cleanup(job_id):
    """Clean up temporary files after download"""
    job_id = re.sub(r'[^a-zA-Z0-9-]', '', job_id)
    job_dir = TEMP_DIR / job_id

    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)

    return jsonify({'status': 'cleaned'})


@app.route('/health')
def health():
    """Health check endpoint for Render"""
    return jsonify({
        'status': 'healthy',
        'api_key_configured': bool(OPENAI_API_KEY)
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)
