"""
Podcast TTS Web App
Converts podcast scripts to audio using OpenAI TTS API
"""

import os
import re
import json
import uuid
import tempfile
import shutil
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, Response, session, redirect, url_for

from openai import OpenAI

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
    """Get OpenAI client"""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    return OpenAI(api_key=OPENAI_API_KEY)


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


def split_into_chunks(text, max_chars=4000):
    """Split text into chunks for OpenAI API (max 4096 chars per request)"""
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

    def generate_stream(text, voice, model):
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

            # Preprocess and split
            processed = preprocess_text(text)
            chunks = split_into_chunks(processed)
            total_chunks = len(chunks)

            yield f"data: {{\"status\": \"processing\", \"message\": \"Starting generation...\", \"total\": {total_chunks}}}\n\n"

            # Get OpenAI client
            client = get_client()

            # Generate audio for each chunk
            chunk_files = []
            for i, chunk in enumerate(chunks):
                chunk_path = job_dir / f"chunk-{i:03d}.mp3"

                yield f"data: {{\"status\": \"processing\", \"message\": \"Generating chunk {i+1} of {total_chunks}...\", \"current\": {i+1}, \"total\": {total_chunks}}}\n\n"

                try:
                    response = client.audio.speech.create(
                        model=model,
                        voice=voice,
                        input=chunk,
                        response_format="mp3"
                    )
                    response.stream_to_file(str(chunk_path))

                    if chunk_path.exists() and chunk_path.stat().st_size > 0:
                        chunk_files.append(chunk_path)
                    else:
                        yield f"data: {{\"status\": \"error\", \"message\": \"Failed to generate chunk {i+1}\"}}\n\n"
                        return

                except Exception as e:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'API error: {str(e)}'})}\n\n"
                    return

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

    response = Response(generate_stream(text, voice, model), mimetype='text/event-stream')
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
