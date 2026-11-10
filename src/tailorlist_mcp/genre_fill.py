"""fill_genres: look up MusicBrainz genres for the artists Spotify leaves without any.

A background thread inside the server process asks MusicBrainz about one artist at a time
(about 1 request per second, so a few hundred artists take a while) and writes each result to
the cache as it goes. The tool call returns at once; calling it again reports progress.

  - Resume: results are saved per artist, and an artist that was asked recently (even with no
    result) is skipped, so a restarted server continues where the last one stopped.
  - One run at a time across processes: a lock row in the cache database (with a heartbeat)
    keeps a second server process (Claude Desktop may start two) from running it in parallel.
    A lock whose heartbeat stopped is taken over.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from tailorlist_mcp import settings, store
from tailorlist_mcp.sources import musicbrainz

JOB = "genre_fill"
RETRY_AFTER = 30 * 24 * 3600      # ask again about "found nothing" after 30 days
STALE_AFTER = 600                 # a lock without a heartbeat for this long is dead
_MAX_FAILURES = 3                 # artists in a row that MusicBrainz couldn't answer: stop and report


def _owner_alive(owner: str) -> bool:
    """False only when the owner's process (the pid in its id) is certainly gone on this machine."""
    try:
        os.kill(int(owner.split("-")[0]), 0)
    except ProcessLookupError:
        return False
    except (ValueError, OSError):
        pass
    return True


def _file_of(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else None


class BackgroundFill:
    """One MusicBrainz job running in a background thread: the shared start/status/lock/resume logic.

    Subclasses say what is pending (_counts, _candidates) and how to handle one row (_process).
    """

    JOB = ""
    RETRY_AFTER = RETRY_AFTER
    SECONDS_PER_ITEM = 2.5
    WHAT = "items"

    def __init__(
        self,
        client_factory: Callable[[], musicbrainz.MusicBrainzClient] = musicbrainz.MusicBrainzClient,
        clock: Callable[[], float] = time.time,
        run_in_thread: bool = True,
    ):
        self._client_factory = client_factory
        self._clock = clock
        self._run_in_thread = run_in_thread
        self._owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.processed = 0       # this run
        self.hits = 0
        self.error: str | None = None

    # ---------------------------------------------------------------- what to do (subclasses)

    def _counts(self, conn: sqlite3.Connection, now: int) -> dict:
        """{"total", "checked", "found", "pending"}."""
        raise NotImplementedError

    def _candidates(self, conn: sqlite3.Connection, now: int) -> list:
        raise NotImplementedError

    def _process(self, client, conn: sqlite3.Connection, row, now: int) -> bool:
        """Look one row up and save the result. True when something was found. Raises MusicBrainzError
        when MusicBrainz couldn't answer (the row then stays pending)."""
        raise NotImplementedError

    # ---------------------------------------------------------------- public

    @property
    def running_here(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, conn: sqlite3.Connection, db_path: Path | None = None) -> dict:
        """Start the background fill unless one is running (here or in another process)."""
        # The job opens its own connection: to the same file as conn (never silently the default cache).
        db_path = db_path or _file_of(conn) or settings.cache_path()
        now = int(self._clock())
        if not self.running_here and self._counts(conn, now)["pending"]:
            if store.acquire_job(conn, self.JOB, self._owner, now, STALE_AFTER, _owner_alive):
                self.processed = self.hits = 0
                self.error = None
                self._stop.clear()
                if self._run_in_thread:
                    self._thread = threading.Thread(target=self._run, args=(db_path,), name=self.JOB, daemon=True)
                    self._thread.start()
                else:
                    self._run(db_path)
        return self.status(conn)

    def status(self, conn: sqlite3.Connection) -> dict:
        now = int(self._clock())
        counts = self._counts(conn, now)
        job = store.job_state(conn, self.JOB, now, STALE_AFTER)
        running = self.running_here or job["running"]
        return {
            **counts,
            "running": running,
            "where": None if not running else ("this server process" if self.running_here else "another server process"),
            "run_processed": self.processed if self.running_here or self.processed else None,
            "run_found": self.hits if self.running_here or self.processed else None,
            "error": self.error or (None if running else job["error"]),
            "seconds_left": counts["pending"] * self.SECONDS_PER_ITEM if running else None,
        }

    def stop(self) -> None:
        self._stop.set()

    # ---------------------------------------------------------------- the job

    def _run(self, db_path: Path) -> None:
        conn = store.connect(db_path)
        error: str | None = None
        try:
            client = self._client_factory()
            failures = 0
            last_failure = ""
            for row in self._candidates(conn, int(self._clock())):
                if self._stop.is_set():
                    break
                try:
                    found = self._process(client, conn, row, int(self._clock()))
                except musicbrainz.MusicBrainzError as exc:   # not saved: the row stays pending
                    failures += 1
                    last_failure = str(exc)
                    if failures >= _MAX_FAILURES:
                        error = f"Stopped after {failures} {self.WHAT} in a row failed: {last_failure}"
                        break
                    continue
                failures = 0
                self.processed += 1
                self.hits += 1 if found else 0
                store.job_heartbeat(conn, self.JOB, self._owner, int(self._clock()))
        except Exception as exc:  # never let the thread die silently
            error = f"{type(exc).__name__}: {exc}"
        finally:
            self.error = error
            try:
                store.release_job(conn, self.JOB, self._owner, error)
            finally:
                conn.close()


class GenreFill(BackgroundFill):
    JOB = JOB
    WHAT = "artists"
    SECONDS_PER_ITEM = 2.5          # about 2-3 requests per artist at 1 per second

    def _counts(self, conn, now):
        return store.mb_counts(conn, now, self.RETRY_AFTER)

    def _candidates(self, conn, now):
        return store.mb_candidates(conn, now, self.RETRY_AFTER)

    def _process(self, client, conn, row, now):
        found = client.lookup(row["id"], row["name"])
        store.save_mb_result(conn, row["id"], found.genres if found else [], found.source if found else None, now)
        return bool(found)


def describe(status: dict) -> str:
    """Text for Claude. Never claims anything is on screen."""
    total, found, checked = status["total"], status["found"], status["checked"]
    if not total:
        return "Every artist in the cache already has genres from Spotify; nothing to fill."
    lines = []
    if status["running"]:
        minutes = max(1, round((status["seconds_left"] or 0) / 60))
        lines.append(
            f"Filling genres from MusicBrainz in the background ({status['where']}): {checked} of {total} artists "
            f"checked, {found} got genres. About {minutes} min left (MusicBrainz allows 1 request per second). "
            "Call fill_genres again to see progress; the other tools use the new genres as they arrive."
        )
    elif status["pending"]:
        lines.append(
            f"Not running. {checked} of {total} artists checked, {found} got genres, {status['pending']} left."
            + (f" The last run stopped early: {status['error']}" if status["error"] else "")
            + " Call fill_genres to resume."
        )
    else:
        lines.append(f"Done. {checked} of {total} artists checked: {found} got genres from MusicBrainz, "
                     f"{checked - found} had no trustworthy match.")
    lines.append(f"({total} = primary artists whose Spotify genres are empty; MusicBrainz is asked only about those.)")
    return "\n".join(lines)


#: The one fill of this server process.
FILL = GenreFill()
