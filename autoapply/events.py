"""
Real-time event bus for the AutoApply pipeline.

Provides a thread-safe singleton that pipeline modules emit events to,
and the Streamlit dashboard consumes from.

Event types cover every observable step:
    - Pipeline lifecycle
    - Job discovery / search
    - Deduplication checks
    - Page loading, classification, navigation
    - Form field detection and filling
    - Resume tailoring
    - Submission decisions
"""

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class EventType(Enum):
    # Pipeline lifecycle
    PIPELINE_START = "pipeline_start"
    PIPELINE_END = "pipeline_end"
    PHASE_START = "phase_start"
    PHASE_END = "phase_end"

    # Discovery
    SEARCH_START = "search_start"
    SEARCH_BOARD_START = "search_board_start"
    SEARCH_RESULT = "search_result"
    SEARCH_BOARD_END = "search_board_end"
    SEARCH_END = "search_end"
    JD_FETCH_START = "jd_fetch_start"
    JD_FETCH_END = "jd_fetch_end"

    # Deduplication
    DEDUP_CHECK = "dedup_check"
    DEDUP_DUPLICATE = "dedup_duplicate"
    DEDUP_NEW = "dedup_new"
    DEDUP_SEMANTIC = "dedup_semantic"

    # Page interaction
    PAGE_LOAD = "page_load"
    PAGE_HTML_CAPTURED = "page_html_captured"
    PAGE_SCREENSHOT = "page_screenshot"
    PAGE_CLASSIFIED = "page_classified"
    PAGE_NAVIGATE_CLICK = "page_navigate_click"
    PAGE_APPLY_FOUND = "page_apply_found"
    PAGE_APPLY_NOT_FOUND = "page_apply_not_found"

    # Form filling
    FORM_FIELDS_DETECTED = "form_fields_detected"
    FORM_FIELD_FILLING = "form_field_filling"
    FORM_FIELD_FILLED = "form_field_filled"
    FORM_FIELD_SKIPPED = "form_field_skipped"
    FORM_FIELD_ERROR = "form_field_error"
    FORM_FILL_COMPLETE = "form_fill_complete"

    # Resume tailoring
    RESUME_TAILOR_START = "resume_tailor_start"
    RESUME_CACHE_HIT = "resume_cache_hit"
    RESUME_AI_CALL = "resume_ai_call"
    RESUME_TAILOR_END = "resume_tailor_end"
    RESUME_PDF_COMPILE = "resume_pdf_compile"

    # Submission
    SUBMIT_DECISION = "submit_decision"
    SUBMIT_AUTO = "submit_auto"
    SUBMIT_QUEUED = "submit_queued"
    SUBMIT_DRY_RUN = "submit_dry_run"
    SUBMIT_SUCCESS = "submit_success"
    SUBMIT_ERROR = "submit_error"

    # Job processing
    JOB_START = "job_start"
    JOB_END = "job_end"
    JOB_SKIP = "job_skip"
    JOB_ERROR = "job_error"
    JOB_DELAY = "job_delay"

    # Auth / verification
    AUTH_WALL_DETECTED = "auth_wall_detected"
    AUTH_LOGIN_ATTEMPT = "auth_login_attempt"
    AUTH_OTP_WAITING = "auth_otp_waiting"
    AUTH_VERIFY_WAITING = "auth_verify_waiting"
    AUTH_PAUSE_FOR_USER = "auth_pause_for_user"
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    AUTH_TIMEOUT = "auth_timeout"

    # General
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class PipelineEvent:
    """A single observable event from the pipeline."""
    event_type: EventType
    message: str
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")

    @property
    def phase(self) -> str:
        return self.data.get("phase", "")

    @property
    def category(self) -> str:
        """Group events by category for UI filtering."""
        name = self.event_type.value
        if name.startswith("search") or name.startswith("jd_fetch"):
            return "discovery"
        if name.startswith("dedup"):
            return "dedup"
        if name.startswith("auth"):
            return "auth"
        if name.startswith("page"):
            return "navigation"
        if name.startswith("form"):
            return "form_filling"
        if name.startswith("resume"):
            return "resume"
        if name.startswith("submit"):
            return "submission"
        if name.startswith("job"):
            return "job"
        if name.startswith("pipeline") or name.startswith("phase"):
            return "pipeline"
        return "general"


class EventBus:
    """
    Thread-safe singleton event bus.

    Pipeline code calls `emit()` to publish events.
    Dashboard polls `get_events()` to consume them.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._events: deque = deque(maxlen=5000)
        self._event_lock = threading.Lock()
        self._listeners = []
        self._current_job: Optional[Dict] = None
        self._pipeline_running = False
        self._pipeline_stats = {
            "total_discovered": 0,
            "total_new": 0,
            "total_applied": 0,
            "total_queued": 0,
            "total_errors": 0,
            "total_skipped": 0,
            "current_phase": "",
            "current_job_index": 0,
            "total_jobs_to_process": 0,
        }
        # Auth pause mechanism
        self._auth_response: Optional[str] = None
        self._auth_response_event = threading.Event()
        self._initialized = True

    def wait_for_auth_response(self, timeout: int = 300) -> Optional[str]:
        """Block until dashboard provides auth input (OTP code, etc.)."""
        self._auth_response_event.clear()
        self._auth_response = None
        got_response = self._auth_response_event.wait(timeout=timeout)
        return self._auth_response if got_response else None

    def provide_auth_response(self, value: str):
        """Called by dashboard when user enters OTP/code."""
        self._auth_response = value
        self._auth_response_event.set()

    def emit(self, event_type: EventType, message: str, **data):
        """Publish an event. Thread-safe."""
        event = PipelineEvent(
            event_type=event_type,
            message=message,
            data=data,
        )
        with self._event_lock:
            self._events.append(event)
        return event

    def get_events(self, since: float = 0, limit: int = 200) -> List[PipelineEvent]:
        """Get events since a timestamp. Thread-safe."""
        with self._event_lock:
            events = [e for e in self._events if e.timestamp > since]
        return events[-limit:]

    def get_all_events(self) -> List[PipelineEvent]:
        """Get all events."""
        with self._event_lock:
            return list(self._events)

    def get_latest(self, n: int = 1) -> List[PipelineEvent]:
        """Get the N most recent events."""
        with self._event_lock:
            return list(self._events)[-n:]

    @property
    def stats(self) -> Dict:
        return dict(self._pipeline_stats)

    def update_stats(self, **kwargs):
        self._pipeline_stats.update(kwargs)

    @property
    def current_job(self) -> Optional[Dict]:
        return self._current_job

    @current_job.setter
    def current_job(self, job: Optional[Dict]):
        self._current_job = job

    @property
    def running(self) -> bool:
        return self._pipeline_running

    @running.setter
    def running(self, val: bool):
        self._pipeline_running = val

    def clear(self):
        """Clear all events and reset stats."""
        with self._event_lock:
            self._events.clear()
        self._current_job = None
        self._pipeline_running = False
        self._pipeline_stats = {
            "total_discovered": 0,
            "total_new": 0,
            "total_applied": 0,
            "total_queued": 0,
            "total_errors": 0,
            "total_skipped": 0,
            "current_phase": "",
            "current_job_index": 0,
            "total_jobs_to_process": 0,
        }

    def event_count(self) -> int:
        with self._event_lock:
            return len(self._events)


# Module-level convenience
bus = EventBus()


def emit(event_type: EventType, message: str, **data):
    """Shortcut: autoapply.events.emit(EventType.X, 'msg', key=val)"""
    return bus.emit(event_type, message, **data)
