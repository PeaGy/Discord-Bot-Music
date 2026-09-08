"""Small, secret-free timing diagnostics shared by the music pipeline."""

import asyncio
from contextlib import contextmanager
import re
from time import perf_counter


_SAFE_LABEL = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def _safe_label(value):
    if isinstance(value, (str, int)) and _SAFE_LABEL.fullmatch(str(value)):
        return str(value)
    return "-"


def log_phase_timing(logger, phase, started, *, guild_id=None, track_id=None, outcome="success"):
    """Log only bounded labels/IDs, never URLs, metadata, or exception text."""
    logger.info(
        "music-timing phase=%s elapsed=%.3fs outcome=%s guild=%s track=%s",
        _safe_label(phase),
        max(0.0, perf_counter() - started),
        outcome if outcome in {"success", "error", "cancelled", "fallback"} else "error",
        _safe_label(guild_id),
        _safe_label(track_id),
    )


@contextmanager
def phase_timer(logger, phase, *, guild_id=None, track_id=None):
    """Measure a synchronous or awaited block without changing its outcome."""
    started = perf_counter()
    outcome = "success"
    try:
        yield
    except BaseException as error:
        outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
        raise
    finally:
        log_phase_timing(
            logger, phase, started, guild_id=guild_id, track_id=track_id, outcome=outcome,
        )
