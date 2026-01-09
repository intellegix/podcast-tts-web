# Podcast TTS Web - System Documentation

## Overview

Enterprise-grade podcast generation system that converts scripts/topics into professional two-person podcast audio using multi-AI orchestration.

**Live URL**: https://podcast-tts-web.onrender.com
**Repository**: https://github.com/intellegix/podcast-tts-web

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (Flask)                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ index.html│  │ style.css │  │pipeline.js│  │ Preview Panel  │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Flask Backend (app.py)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Rate Limiter │  │ Auth Layer  │  │ Job Queue (job_store.py)│  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Interactive Pipeline                          │
│                                                                  │
│  ANALYZE → RESEARCH → EXPAND → ENHANCE → GENERATE → COMBINE     │
│     │          │          │        │          │          │       │
│     ▼          ▼          ▼        ▼          ▼          ▼       │
│  [Pause]   [Pause]    [Pause]  [Pause]    [Pause]    [Done]     │
│  User can provide suggestions at each pause point               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      AI Services                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │  Perplexity │  │   Claude    │  │       OpenAI TTS        │  │
│  │  (Research) │  │  (Enhance)  │  │   (Audio Generation)    │  │
│  │ 3-8 agents  │  │   Sonnet    │  │   tts-1 / tts-1-hd      │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## File Structure

```
podcast-tts-web/
├── app.py              # Main Flask application (~2700 lines)
├── job_store.py        # Job state management & length configs
├── requirements.txt    # Python dependencies
├── render.yaml         # Render deployment config
├── .gitignore
├── CLAUDE.md           # This documentation
├── static/
│   ├── style.css       # UI styling with glassmorphism
│   └── pipeline.js     # Frontend pipeline visualization
└── templates/
    ├── index.html      # Main app UI
    └── login.html      # Password authentication
```

---

## Pipeline Stages

### 1. ANALYZE (Script Analysis)
- Parses input text into episodes
- Detects completeness (outline vs full script)
- Identifies speakers and dialogue structure

### 2. RESEARCH (Multi-Agent Perplexity)
- Spawns 3-8 parallel research agents based on podcast length
- Each agent researches different angle:
  - Statistics & data
  - Recent news (2024-2025)
  - Expert opinions
  - Case studies
  - Misconceptions
  - Historical context
  - Future trends
  - Debates/viewpoints
  - Practical applications
  - Societal impact
- Returns citations and research context

### 3. EXPAND (GPT-4o Expansion)
- Converts outlines/bullets to full dialogue
- Integrates research findings
- Follows length-specific word targets
- Maintains ALEX/SARAH speaker format

### 4. ENHANCE (Claude Dialogue Polish)
- Improves natural flow and pacing
- Adds educational explanations
- TTS-optimized formatting
- Ensures all topics covered

### 5. GENERATE (OpenAI TTS)
- Splits text into chunks by speaker
- Multi-voice assignment:
  - ALEX → echo (male)
  - SARAH → shimmer (female)
- Generates MP3 chunks with retry logic

### 6. COMBINE (Final Assembly)
- Concatenates audio chunks
- Generates transcript file
- Creates downloadable podcast

---

## Podcast Length Options

| Option | Duration | Research Agents | Word Target | Detail Level |
|--------|----------|-----------------|-------------|--------------|
| Quick | 3-5 min | 3 | 600 | Minimal - key points only |
| Short | 5-10 min | 4 | 1,200 | Concise - brief examples |
| Medium | 10-15 min | 5 | 2,000 | Balanced - good coverage |
| Long | 15-25 min | 6 | 3,500 | Detailed - in-depth |
| Extended | 25-40 min | 8 | 6,000 | Comprehensive - deep-dive |

**CRITICAL**: All topics are ALWAYS covered regardless of length. Length only controls detail level, never skips content.

---

## API Endpoints

### Authentication
- `GET /login` - Login page
- `POST /login` - Authenticate with password
- `GET /logout` - Clear session

### Interactive Pipeline
- `POST /api/job` - Create new pipeline job
  - Form params: `text`, `voice`, `model`, `multi_voice`, `ai_enhance`, `auto_expand`, `target_length`
- `GET /api/job/<job_id>/status` - Poll job status (rate-limit exempt)
- `POST /api/job/<job_id>/continue` - Submit suggestion and continue
  - JSON body: `{"suggestion": "optional user guidance"}`
- `DELETE /api/job/<job_id>` - Cleanup job files

### Streaming (Legacy)
- `POST /generate` - SSE streaming generation (non-interactive)

### Utilities
- `GET /health` - System health check
- `GET /download/<download_id>` - Download generated audio
- `GET /transcript/<transcript_id>` - Download transcript

---

## Key Classes (job_store.py)

### JobStatus (Enum)
```python
CREATED = "created"
RUNNING = "running"
PAUSED_FOR_REVIEW = "paused_for_review"
COMPLETE = "complete"
ERROR = "error"
```

### PodcastLength (Enum)
```python
QUICK = "quick"      # ~3-5 minutes
SHORT = "short"      # ~5-10 minutes
MEDIUM = "medium"    # ~10-15 minutes (default)
LONG = "long"        # ~15-25 minutes
EXTENDED = "extended"  # ~25-40 minutes
```

### Stage (Enum)
```python
ANALYZE = "analyze"
RESEARCH = "research"
EXPAND = "expand"
ENHANCE = "enhance"
GENERATE = "generate"
COMBINE = "combine"
```

### Job (Dataclass)
```python
@dataclass
class Job:
    id: str
    status: JobStatus
    current_stage: Optional[Stage]
    text: str
    voice: str
    model: str
    multi_voice: bool
    ai_enhance: bool
    auto_expand: bool
    target_length: PodcastLength
    stage_results: Dict[Stage, StageResult]
    user_suggestions: Dict[Stage, str]
    episodes: List[dict]
    research_map: Dict[int, dict]
    final_text: str
```

---

## Key Functions (app.py)

### Pipeline Stage Functions
- `run_stage_analyze(job)` - Parse and detect episodes
- `run_stage_research(job)` - Multi-agent Perplexity research
- `run_stage_expand(job)` - GPT-4o script expansion
- `run_stage_enhance(job)` - Claude dialogue enhancement
- `run_stage_generate(job)` - OpenAI TTS audio generation
- `run_stage_combine(job)` - Final audio assembly

### Research Functions
- `research_episode_parallel(episode_data, suggestion, num_agents, depth)` - Parallel research
- `query_single_angle(angle)` - Single Perplexity query

### Text Processing
- `parse_episodes(text)` - Split text into episodes
- `detect_speakers(text)` - Find speaker labels
- `split_by_speaker(text, voice_assignments)` - Chunk by speaker
- `preprocess_text(text)` - Clean for TTS

### Audio Functions
- `call_openai_tts_with_retry(client, model, voice, text)` - TTS with retry
- `concatenate_mp3_files(files, output_path)` - Join audio chunks

---

## Environment Variables

```bash
# Required
OPENAI_API_KEY=sk-...           # OpenAI API key for TTS
CLAUDE_API_KEY=sk-ant-...       # Anthropic API key for enhancement
PERPLEXITY_API_KEY=pplx-...     # Perplexity API key for research
PASSWORD=...                     # App access password

# Optional
SECRET_KEY=...                   # Flask session secret (auto-generated)
FLASK_ENV=production             # Enable secure cookies
```

---

## Deployment (Render)

### render.yaml
```yaml
services:
  - type: web
    name: podcast-tts-web
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn app:app --bind 0.0.0.0:$PORT --worker-class gevent --timeout 300
```

### Auto-Deploy
- Pushes to `master` branch trigger automatic deployment
- Free tier spins down after inactivity (~50s cold start)

---

## Voice Options

| Voice | Character | Description |
|-------|-----------|-------------|
| nova | - | Warm, natural female |
| alloy | - | Neutral, versatile |
| echo | ALEX | Deeper male voice |
| fable | - | British accent |
| onyx | - | Deep male |
| shimmer | SARAH | Expressive female |

### Multi-Voice Mode
When enabled, auto-detects speakers and assigns:
- Male names (Alex, John, etc.) → echo
- Female names (Sarah, Emma, etc.) → shimmer
- Unknown → alloy

---

## Rate Limiting

- Default: 200/day, 50/hour per IP
- `/api/job` (create): 10/hour
- `/api/job/<id>/status`: Exempt (polling endpoint)
- Perplexity: Custom rate limiter for API limits

---

## Educational Enhancement

Claude is instructed to:
1. Explain ALL technical concepts in simple terms
2. Use everyday analogies and real-world examples
3. Break down jargon naturally in dialogue
4. Connect new concepts to familiar things
5. Make learning feel conversational, not lecture-like
6. ALEX asks clarifying questions
7. SARAH uses "Think of it like..." phrases

---

## Security Features

- Password-protected access
- Session cookies (HTTPOnly, Secure, SameSite=Strict)
- CSRF protection via Flask-WTF
- Input validation and sanitization
- Rate limiting on sensitive endpoints
- File extension whitelist (.txt, .md)
- Max file size: 100MB
- Max text length: 5MB

---

## Common Issues & Solutions

### 429 Rate Limit Errors
- Status polling endpoint should be exempt (`@limiter.exempt`)
- Perplexity has built-in rate limiter

### Empty Audio Files
- Check OpenAI API key is valid
- Verify TTS response has `stream_to_file()` method
- Check file size after generation

### Type Errors in Pipeline
- `split_by_speaker()` returns dicts, convert to tuples
- Path operations use `/` not `\`

### Missing Research
- Verify PERPLEXITY_API_KEY is set
- Check rate limiter isn't blocking

---

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python app.py

# Run with gunicorn (production-like)
gunicorn app:app --worker-class gevent --timeout 300

# Check health
curl http://localhost:5000/health
```

---

## Recent Changes

### 2026-01-09
- Added podcast length control (Quick/Short/Medium/Long/Extended)
- Added educational enhancement prompts
- Fixed rate limiting on status polling endpoint
- Fixed TTS function calls and multi-voice chunk handling
- Added interactive pipeline with user suggestions

---

## Contact

**Project Owner**: Austin Kidwell
**Company**: Intellegix / ASR Inc
**Location**: San Diego, CA
