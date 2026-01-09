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

    @classmethod
    def get_config(cls, length: 'PodcastLength') -> dict:
        """Get configuration parameters for each length option"""
        # IMPORTANT: All configs emphasize covering ALL topics - length only affects detail level
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
                'expand_instruction': 'CRITICAL: Cover ALL topics and points from the source material comprehensively. Provide balanced coverage with good examples and context for each point. Include interesting details and ensure nothing is skipped.',
                'enhance_instruction': 'Create natural, educational dialogue that thoroughly covers ALL topics. Include good examples for each point. Balance entertainment and information while ensuring complete coverage.'
            },
            cls.LONG: {
                'display_name': 'Long (~15-25 min)',
                'description': 'In-depth exploration with examples',
                'research_agents': 6,
                'research_depth': 'deep',
                'detail_level': 'detailed',
                'word_target': 3500,
                'expand_instruction': 'CRITICAL: Cover ALL topics and points from the source material in depth. Explore each topic thoroughly with multiple examples, analogies, and real-world applications. Add interesting tangents while ensuring every original point is well-developed.',
                'enhance_instruction': 'Create rich, detailed dialogue that deeply explores ALL topics. Include multiple examples per topic, fun tangents, and deeper explanations. Let conversations breathe while ensuring nothing from the source is missed.'
            },
            cls.EXTENDED: {
                'display_name': 'Extended (~25-40 min)',
                'description': 'Comprehensive deep-dive',
                'research_agents': 8,
                'research_depth': 'exhaustive',
                'detail_level': 'comprehensive',
                'word_target': 6000,
                'expand_instruction': 'CRITICAL: Cover ALL topics and points from the source material exhaustively. Create comprehensive coverage with extensive examples, case studies, historical context, and expert perspectives for each point. Explore tangents and nuances while ensuring every topic is thoroughly addressed.',
                'enhance_instruction': 'Create immersive, podcast-style dialogue with extensive exploration of ALL topics. Include multiple anecdotes, deep explanations, listener Q&A style segments, and thorough coverage of every aspect mentioned in the source material.'
            }
        }
        return configs.get(length, configs[cls.MEDIUM])


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

    def get_length_config(self) -> dict:
        """Get configuration for current target length"""
        return PodcastLength.get_config(self.target_length)

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
            'transcript_id': self.transcript_id
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
