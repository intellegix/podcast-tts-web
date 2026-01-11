"""
Job State Management for Interactive Pipeline

Manages job state for the staged podcast generation pipeline,
allowing users to pause between stages and provide suggestions.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum
import threading
import logging

logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """Pipeline job status states"""
    CREATED = "created"
    RUNNING = "running"
    PAUSED_FOR_REVIEW = "paused_for_review"
    COMPLETE = "complete"
    ERROR = "error"


class PodcastLength(Enum):
    """Target podcast length options"""
    QUICK = "quick"      # ~3-5 minutes - brief summary
    SHORT = "short"      # ~5-10 minutes - concise coverage
    MEDIUM = "medium"    # ~10-15 minutes - balanced detail
    LONG = "long"        # ~15-25 minutes - in-depth exploration
    EXTENDED = "extended"  # ~25-40 minutes - comprehensive deep-dive
    COMPREHENSIVE = "comprehensive"  # No time limit - processes ALL content with full detail

    @classmethod
    def get_config(cls, length: 'PodcastLength', mode: 'PodcastMode' = None) -> dict:
        """Get configuration parameters for each length option with mode-specific customization"""
        # Import here to avoid circular import
        if mode is None:
            mode = PodcastMode.EDUCATIONAL
        configs = {
            cls.QUICK: {
                'display_name': 'Quick (~3-5 min)',
                'description': 'Brief summary, key points only',
                'research_agents': 3,
                'research_depth': 'surface',
                'detail_level': 'minimal',
                'word_target': 600,
                'expand_instruction': 'CRITICAL: Cover ALL topics and points from the source material - do not skip anything. Keep explanations brief but touch on every single point mentioned. Summarize each topic in 1-2 sentences. No tangents, but ensure complete coverage.',
                'enhance_instruction': 'Create a quick, punchy dialogue that covers ALL topics. Get to the point fast. Minimal banter but ensure every topic from the source is mentioned, even briefly.'
            },
            cls.SHORT: {
                'display_name': 'Short (~5-10 min)',
                'description': 'Concise coverage of main topics',
                'research_agents': 4,
                'research_depth': 'moderate',
                'detail_level': 'concise',
                'word_target': 1200,
                'expand_instruction': 'CRITICAL: Cover ALL topics and points from the source material - do not skip anything. Keep explanations concise with 1 brief example per major topic. Every point mentioned in the source must appear in the output.',
                'enhance_instruction': 'Create engaging but efficient dialogue that covers ALL topics. Include 1-2 examples per topic. Every point from the source must be addressed, even if briefly.'
            },
            cls.MEDIUM: {
                'display_name': 'Medium (~10-15 min)',
                'description': 'Balanced detail and engagement',
                'research_agents': 5,
                'research_depth': 'thorough',
                'detail_level': 'balanced',
                'word_target': 2000,
                'expand_instruction': 'CRITICAL: You MUST generate approximately 2000 words of dialogue (about 13 minutes of audio). Cover ALL topics comprehensively with good examples and context. Include multiple examples per topic, interesting anecdotes, and ensure thorough exploration. DO NOT be brief - expand fully.',
                'enhance_instruction': 'Create natural, educational two-host podcast dialogue between ALEX and SARAH that is approximately 2000 words long (13 minutes of audio). CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels. Thoroughly cover ALL topics with multiple examples per point. Add explanations, analogies, and natural back-and-forth conversation. DO NOT shorten or summarize.'
            },
            cls.LONG: {
                'display_name': 'Long (~15-25 min)',
                'description': 'In-depth exploration with examples',
                'research_agents': 6,
                'research_depth': 'deep',
                'detail_level': 'detailed',
                'word_target': 3500,
                'expand_instruction': 'CRITICAL: You MUST generate approximately 3500 words of dialogue (about 23 minutes of audio). Explore ALL topics in great depth with multiple examples, analogies, case studies, and real-world applications per topic. Add interesting tangents and detailed explanations. DO NOT be brief.',
                'enhance_instruction': 'Create rich, detailed two-host podcast dialogue between ALEX and SARAH that is approximately 3500 words long (23 minutes of audio). CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels. Deeply explore ALL topics with multiple examples and anecdotes per point. Include fun tangents, deeper explanations, and natural back-and-forth conversation. DO NOT shorten.'
            },
            cls.EXTENDED: {
                'display_name': 'Extended (Unlimited)',
                'description': 'Unlimited duration - covers ALL content in exhaustive detail',
                'research_agents': 8,
                'research_depth': 'exhaustive',
                'detail_level': 'comprehensive',
                'word_target': None,  # No word limit - content determines length
                'minimum_coverage': 1.0,  # 100% topic coverage required
                'expand_instruction': 'CRITICAL: You MUST cover every single topic and point from the source material. There is NO word limit or time constraint. Generate exhaustive dialogue with extensive examples, case studies, historical context, expert perspectives, detailed tangents, and comprehensive exploration for every point. Length should be determined by content richness and thoroughness, not arbitrary targets.',
                'enhance_instruction': 'Create immersive, natural two-host podcast dialogue between ALEX and SARAH with NO word limit - length is determined by content depth and thoroughness. CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels. Cover ALL topics extensively with multiple examples, anecdotes, deep explanations, natural back-and-forth conversation, detailed case studies, and exhaustive coverage of every aspect. Generate as much content as needed for complete coverage.'
            },
            cls.COMPREHENSIVE: {
                'display_name': 'Comprehensive (Process ALL Content)',
                'description': 'No time limit - covers ALL provided content with full detail',
                'research_agents': 8,
                'research_depth': 'exhaustive',
                'detail_level': 'comprehensive',
                'word_target': None,  # No word limit - content determines length
                'minimum_coverage': 1.0,  # 100% topic coverage required
                'expand_instruction': 'CRITICAL: You MUST cover every single topic and detail provided in the source material. There is NO word limit or time constraint. Generate comprehensive dialogue that explores ALL topics with extensive examples, case studies, expert perspectives, and detailed explanations. Length should be determined by content richness, not arbitrary targets. Minimum 3-5 minutes per major topic.',
                'enhance_instruction': 'Create comprehensive two-host podcast dialogue between ALEX and SARAH covering ALL provided content. CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels throughout. There is NO word limit - length is determined by content depth. Every single topic from the source must receive thorough coverage with multiple examples, analogies, and detailed explanations. Maintain natural back-and-forth conversation between the hosts while ensuring 100% content coverage. This should be as long as needed to cover everything properly.'
            }
        }

        # Get base configuration for the length
        config = configs.get(length, configs[cls.MEDIUM]).copy()

        # Apply mode-specific customizations
        if mode == PodcastMode.COMEDY:
            # Comedy mode: length-specific professional comedy techniques
            word_target = config.get('word_target', 2000)

            # Length-specific comedy technique specialization
            if length == cls.QUICK:
                # Quick (3-5 min): Focus on observational humor, rapid-fire observations
                config['enhance_instruction'] = f'Create comedy talk show dialogue between ALEX and SARAH (~{word_target} words). CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels throughout. Apply Seinfeld Steps 1-3: Start with naturally funny topics, extract 2-3 jokes per topic, assemble logically. Use OBSERVATIONAL COMEDY and quick setup/punchline structures. Focus on "Have you ever noticed..." observations and immediate punchlines. Keep pace fast and punchy.'
            elif length == cls.SHORT:
                # Short (5-10 min): Add wordplay and better timing
                config['enhance_instruction'] = f'Create comedy talk show dialogue between ALEX and SARAH (~{word_target} words). CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels throughout. Apply Seinfeld Steps 1-4: Include observational humor, wordplay, and unexpected answers. Use setup/punchline structure with misdirection. Add comedic timing with pauses. ALEX provides setups, SARAH delivers punchlines.'
            elif length == cls.MEDIUM:
                # Medium (10-15 min): Full Seinfeld process, callbacks, character development
                config['enhance_instruction'] = f'Create comedy talk show dialogue between ALEX and SARAH (~{word_target} words). CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels throughout. Apply FULL Seinfeld 5-step process. Include observational, wordplay, juxtaposition, and exaggeration. Develop ALEX/SARAH chemistry with callbacks to earlier topics. Use compression technique for cascading laughter. Include 2025 cultural references.'
            elif length == cls.LONG:
                # Long (15-25 min): Advanced humor types, extensive callbacks
                config['enhance_instruction'] = f'Create comedy talk show dialogue between ALEX and SARAH (~{word_target} words). CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels throughout. Use MULTIPLE humor types: observational, self-deprecating, surreal, wordplay, unexpected answers, juxtaposition. Extensive callback development with internal logic systems. Apply compression technique and heightening. Include 2025 cultural references and sophisticated character dynamics.'
            elif length == cls.EXTENDED:
                # Extended (25-40 min): Full professional comedy toolkit
                config['enhance_instruction'] = f'Create comedy talk show dialogue between ALEX and SARAH (~{word_target} words). CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels throughout. Use ALL 8 professional humor types extensively. Apply full Seinfeld 5-step process with sophisticated compression technique for "the roll" of cascading laughter. Extensive callback development, advanced character dynamics with "Yes, And" principle. Include comprehensive 2025 cultural integration. Create professional-grade comedy using established techniques.'
            elif length == cls.COMPREHENSIVE:
                # Comprehensive: Maximum professional comedy development
                config['enhance_instruction'] = f'Create comprehensive comedy talk show dialogue between ALEX and SARAH. CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels throughout. Apply MAXIMUM professional comedy development using ALL humor types, full Seinfeld process, extensive callbacks, sophisticated character chemistry, and comprehensive 2025 cultural integration. No time limit - length determined by comedic potential. Professional-grade sophisticated humor throughout.'
            else:
                # Default fallback
                config['enhance_instruction'] = f'Create comedy talk show dialogue between ALEX and SARAH (~{word_target} words). CRITICAL: MUST use proper dialogue format with ALEX: and SARAH: speaker labels throughout. Apply professional comedy techniques with entertainment focus.'
        else:
            # Educational mode: comprehensive coverage, all topics required
            # Keep existing educational enhance_instruction as-is
            pass

        return config


class PodcastMode(Enum):
    """Podcast style/tone options"""
    EDUCATIONAL = "educational"
    COMEDY = "comedy"

    @classmethod
    def get_config(cls, mode: 'PodcastMode') -> dict:
        """Get configuration parameters for each mode option"""
        configs = {
            cls.EDUCATIONAL: {
                'display_name': 'Educational',
                'description': 'Informative, comprehensive coverage of all topics',
                'topic_coverage_required': True,
                'research_angles': [
                    'current_statistics_data',
                    'expert_opinions_analysis',
                    'case_studies_examples',
                    'historical_context_trends',
                    'recent_news_2024_2025',
                    'common_misconceptions',
                    'future_predictions_trends',
                    'debate_controversy_viewpoints'
                ]
            },
            cls.COMEDY: {
                'display_name': 'Comedy Talk Show',
                'description': 'Conversational comedy between ALEX and SARAH',
                'topic_coverage_required': False,  # Key difference - can skip unfunny topics
                'research_angles': [
                    'observational_comedy_angles',
                    'setup_punchline_material',
                    '2025_cultural_references',
                    'workplace_comedy_angles',
                    'technology_humor_angles',
                    'relatable_frustrations',
                    'self_deprecating_material',
                    'callback_opportunities'
                ]
            }
        }
        return configs.get(mode, configs[cls.EDUCATIONAL])


class Stage(Enum):
    """Pipeline stages in order"""
    ANALYZE = "analyze"
    RESEARCH = "research"
    EXPAND = "expand"
    ENHANCE = "enhance"
    GENERATE = "generate"
    COMBINE = "combine"

    @classmethod
    def get_next(cls, current: 'Stage') -> Optional['Stage']:
        """Get the next stage after current, or None if at end"""
        stages = list(cls)
        try:
            idx = stages.index(current)
            if idx < len(stages) - 1:
                return stages[idx + 1]
        except ValueError:
            pass
        return None

    @classmethod
    def get_display_name(cls, stage: 'Stage') -> str:
        """Get human-readable stage name"""
        names = {
            cls.ANALYZE: "Script Analysis",
            cls.RESEARCH: "Topic Research",
            cls.EXPAND: "Script Expansion",
            cls.ENHANCE: "Dialogue Enhancement",
            cls.GENERATE: "Audio Generation",
            cls.COMBINE: "Final Assembly"
        }
        return names.get(stage, stage.value.title())


@dataclass
class StageResult:
    """Result from a completed pipeline stage"""
    stage: Stage
    output_preview: str  # What to show the user for review
    full_output: Any     # Full data needed by next stage
    citations: List[str] = field(default_factory=list)
    changes: List[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    completed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Job:
    """Pipeline job with all state"""
    id: str
    status: JobStatus
    current_stage: Optional[Stage]

    # Input data
    text: str
    voice: str
    model: str
    multi_voice: bool
    ai_enhance: bool
    auto_expand: bool
    target_length: PodcastLength = PodcastLength.MEDIUM
    target_mode: PodcastMode = PodcastMode.EDUCATIONAL

    def get_length_config(self) -> dict:
        """Get configuration for current target length"""
        return PodcastLength.get_config(self.target_length)

    def get_mode_config(self) -> dict:
        """Get configuration for current podcast mode"""
        return PodcastMode.get_config(self.target_mode)

    # Results and suggestions
    stage_results: Dict[Stage, StageResult] = field(default_factory=dict)
    user_suggestions: Dict[Stage, str] = field(default_factory=dict)

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    error_message: Optional[str] = None
    download_id: Optional[str] = None
    transcript_id: Optional[str] = None

    # Parsed data (populated during processing)
    episodes: List[dict] = field(default_factory=list)
    research_map: Dict[int, dict] = field(default_factory=dict)
    enhanced_map: Dict[int, str] = field(default_factory=dict)
    final_text: str = ""
    detected_topics: List[str] = field(default_factory=list)  # Topics detected in analyze stage for mandatory coverage

    # News format metadata (for Perplexity Personalized News Threads)
    is_news_format: bool = False
    news_metadata: Dict[str, Any] = field(default_factory=dict)  # Maps topic title -> NewsTopicMetadata
    news_urls: List[str] = field(default_factory=list)  # Extracted URLs for research

    # User-provided source integration
    user_sources: List[Dict[str, str]] = field(default_factory=list)  # User-provided citations from input
    research_scaling_applied: bool = False  # Whether dynamic scaling was used
    total_research_agents: int = 0  # Actual agents used (may exceed base config)

    def to_dict(self) -> dict:
        """Convert job to JSON-serializable dict"""
        return {
            'id': self.id,
            'status': self.status.value,
            'current_stage': self.current_stage.value if self.current_stage else None,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'error_message': self.error_message,
            'download_id': self.download_id,
            'transcript_id': self.transcript_id,
            'is_news_format': self.is_news_format,
            'news_url_count': len(self.news_urls),
            'detected_topic_count': len(self.detected_topics)
        }

    def get_stage_preview(self) -> Optional[dict]:
        """Get preview data for current stage if paused"""
        if self.status != JobStatus.PAUSED_FOR_REVIEW or not self.current_stage:
            return None

        result = self.stage_results.get(self.current_stage)
        if not result:
            return None

        return {
            'stage': self.current_stage.value,
            'stage_name': Stage.get_display_name(self.current_stage),
            'preview': result.output_preview,
            'citations': result.citations,
            'changes': result.changes,
            'metadata': result.metadata
        }


class JobStore:
    """Thread-safe in-memory job store"""

    def __init__(self, max_jobs: int = 100, cleanup_after_hours: int = 24):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.RLock()
        self._max_jobs = max_jobs
        self._cleanup_hours = cleanup_after_hours
        logger.info(f"JobStore initialized (max_jobs={max_jobs})")

    def create_job(self, job: Job) -> str:
        """Create a new job, return job ID"""
        with self._lock:
            # Cleanup old jobs if at capacity
            if len(self._jobs) >= self._max_jobs:
                self._cleanup_old_jobs()

            self._jobs[job.id] = job
            logger.info(f"Job {job.id} created")
            return job.id

    def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID"""
        with self._lock:
            return self._jobs.get(job_id)

    def update_job(self, job: Job) -> None:
        """Update an existing job"""
        with self._lock:
            job.updated_at = datetime.utcnow()
            self._jobs[job.id] = job
            logger.debug(f"Job {job.id} updated: status={job.status.value}, stage={job.current_stage}")

    def delete_job(self, job_id: str) -> bool:
        """Delete a job, return True if existed"""
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                logger.info(f"Job {job_id} deleted")
                return True
            return False

    def list_jobs(self, status: Optional[JobStatus] = None) -> List[Job]:
        """List all jobs, optionally filtered by status"""
        with self._lock:
            jobs = list(self._jobs.values())
            if status:
                jobs = [j for j in jobs if j.status == status]
            return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def _cleanup_old_jobs(self) -> int:
        """Remove jobs older than cleanup_hours, return count removed"""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=self._cleanup_hours)

        removed = 0
        for job_id, job in list(self._jobs.items()):
            if job.created_at < cutoff:
                del self._jobs[job_id]
                removed += 1

        if removed:
            logger.info(f"Cleaned up {removed} old jobs")
        return removed


# Global singleton instance
job_store = JobStore()
