"""
Background analysis, fully separated from the web API.

Analysis (noise cleanup, Whisper, Qwen, saving) runs in separate worker
PROCESSES, not threads, so it can never slow down API requests: separate
memory, separate Python interpreter (no shared GIL), and below-normal CPU priority.

The queue is the analysis_jobs table:
  queued -> processing -> done | failed

  - Workers claim jobs atomically, so several workers never take the same job.
  - A worker sends a heartbeat while analyzing; if it dies, the job is put back
    in the queue (up to MAX_ATTEMPTS).
  - When idle, worker #0 translates new free-text survey answers and keeps each
    product's single consolidated report up to date (after new or deleted surveys).

How workers run (environment variables, set before starting uvicorn):
  FMCG_WORKER_MODE=auto       (default) the API starts the workers itself
  FMCG_WORKER_MODE=external   start them yourself:  python -m app.worker
                              (e.g. on another machine with a GPU)
  FMCG_ANALYSIS_WORKERS=1     how many analyses run at the same time (default 1;
                              on a CPU, 1 is fastest per video; add more with a GPU
                              or more cores)
"""

import asyncio
import atexit
import inspect
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app.database.database import SessionLocal
from app.database.models import AnalysisJob

BACKEND_DIR = Path(__file__).resolve().parents[2]
HEARTBEAT_FILE = BACKEND_DIR / "uploads" / "queue" / ".api_alive"

POLL_SECONDS = 2  # how often an idle worker checks the queue
JOB_HEARTBEAT_SECONDS = 15  # a running job reports it's alive this often
STALE_AFTER = timedelta(minutes=3)  # no heartbeat for this long = worker died
MAX_ATTEMPTS = 3
PARENT_STALE_SECONDS = 60  # auto mode: workers stop if the API stops

_children: list = []
_started = False


# ============================================================
# API side: start and watch the worker processes
# ============================================================

def start():
    """Called once by the API at startup."""
    global _started
    if _started:
        return
    _started = True
    mode = os.environ.get("FMCG_WORKER_MODE", "auto").strip().lower()
    if mode in ("external", "off"):
        print(f"Analysis workers: mode '{mode}', not started by the API. Run:  python -m app.worker")
        return

    count = max(1, int(os.environ.get("FMCG_ANALYSIS_WORKERS", "1") or 1))
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _touch_heartbeat()
    threading.Thread(target=_heartbeat_loop, name="api-heartbeat", daemon=True).start()

    env = dict(os.environ, FMCG_API_HEARTBEAT=str(HEARTBEAT_FILE))
    for index in range(count):
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.worker", "--index", str(index)],
            cwd=str(BACKEND_DIR),
            env=env,
        )
        _children.append(proc)
    atexit.register(_stop_children)
    print(f"Analysis workers started: {count} process(es), separate from the API.")


def notify():
    """Kept for compatibility: workers poll the queue every few seconds."""
    return None


def _touch_heartbeat():
    try:
        HEARTBEAT_FILE.write_text(str(time.time()))
    except Exception:
        pass


def _heartbeat_loop():
    while True:
        _touch_heartbeat()
        time.sleep(10)


def _stop_children():
    for proc in _children:
        try:
            proc.terminate()
        except Exception:
            pass


# ============================================================
# Worker side
# ============================================================

def _lower_priority():
    """The API should always get the CPU first."""
    try:
        if os.name == "nt":
            import ctypes

            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
    except Exception:
        pass


def _parent_gone() -> bool:
    path = os.environ.get("FMCG_API_HEARTBEAT")
    if not path:
        return False  # external mode: runs until stopped
    try:
        return time.time() - float(Path(path).read_text().strip() or 0) > PARENT_STALE_SECONDS
    except Exception:
        return True


def requeue_stale(db) -> int:
    """Jobs whose worker stopped sending heartbeats go back in the queue."""
    cutoff = datetime.utcnow() - STALE_AFTER
    stale = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.status == "processing", AnalysisJob.heartbeat_at < cutoff)
        .all()
    )
    for job in stale:
        if (job.attempts or 0) >= MAX_ATTEMPTS:
            job.status = "failed"
            job.error = "The analysis stopped repeatedly. Try uploading the recording again."
            job.finished_at = datetime.utcnow()
        else:
            job.status = "queued"
            job.worker_id = None
    if stale:
        db.commit()
        print(f"Analysis worker: {len(stale)} stalled job(s) recovered.")
    return len(stale)


def claim_next(db, worker_id: str):
    """Atomically take the oldest queued job; None if the queue is empty."""
    for _ in range(5):
        candidate = (
            db.query(AnalysisJob.id)
            .filter(AnalysisJob.status == "queued")
            .order_by(AnalysisJob.id.asc())
            .first()
        )
        if not candidate:
            return None
        now = datetime.utcnow()
        # Only succeeds if nobody else claimed it in between.
        claimed = db.execute(
            text(
                "UPDATE analysis_jobs SET status = 'processing', worker_id = :w, started_at = :now, "
                "heartbeat_at = :now, attempts = COALESCE(attempts, 0) + 1 "
                "WHERE id = :id AND status = 'queued'"
            ),
            {"w": worker_id, "now": now, "id": candidate.id},
        ).rowcount
        db.commit()
        if claimed == 1:
            job = db.get(AnalysisJob, candidate.id)
            db.refresh(job)
            return job.id, job.stored_path, job.filename, job.response_id, job.kind or "analyze", job.target_feedback_id
    return None


def _finish(job_id, **fields):
    db = SessionLocal()
    try:
        job = db.get(AnalysisJob, job_id)
        if job:
            for k, v in fields.items():
                setattr(job, k, v)
            job.finished_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def _beat(job_id, stop: threading.Event):
    while not stop.wait(JOB_HEARTBEAT_SECONDS):
        db = SessionLocal()
        try:
            db.query(AnalysisJob).filter(AnalysisJob.id == job_id).update(
                {AnalysisJob.heartbeat_at: datetime.utcnow()}, synchronize_session=False
            )
            db.commit()
        except Exception:
            pass
        finally:
            db.close()


async def _call_upload(path: str, filename: str, response_id):
    """Run the existing /video/upload function on a saved file (unchanged)."""
    from fastapi import UploadFile

    from app.api.video import upload_video

    params = inspect.signature(upload_video).parameters
    handle = open(path, "rb")
    kwargs = {"file": UploadFile(file=handle, filename=filename)}
    for name, p in params.items():
        if name == "file":
            continue
        if name == "response_id":
            kwargs[name] = response_id
            continue
        default = p.default
        kwargs[name] = getattr(default, "default", default) if default is not inspect.Parameter.empty else None
    try:
        return await upload_video(**kwargs)
    finally:
        try:
            handle.close()
        except Exception:
            pass


# Columns that identify a record and must survive a re-analysis.
KEEP_ON_REANALYSIS = {"id", "original_filename", "video_path", "uploaded_at", "response_id"}


def apply_reanalysis(new_id: int, target_id: int) -> int:
    """
    Copy a fresh analysis (new_id) into the existing record (target_id): same id,
    survey link and recording; every result column replaced. Then remove the
    temporary record and its duplicate media file. Returns the product id (or None).
    """
    from app.database.models import FeedbackRecord, SurveyResponse

    db = SessionLocal()
    try:
        fresh = db.get(FeedbackRecord, new_id)
        target = db.get(FeedbackRecord, target_id)
        if fresh is None or target is None:
            raise RuntimeError("The re-analyzed record could not be found.")
        for column in FeedbackRecord.__table__.columns:  # every column, including ones added later
            if column.key not in KEEP_ON_REANALYSIS:
                setattr(target, column.key, getattr(fresh, column.key))
        duplicate_media = fresh.video_path if fresh.video_path != target.video_path else None
        db.delete(fresh)
        db.commit()
        if duplicate_media:
            try:
                Path(duplicate_media).unlink(missing_ok=True)
            except Exception:
                pass
        if target.response_id:
            survey = db.get(SurveyResponse, target.response_id)
            if survey:
                survey.status = "analyzed"
                db.commit()
                return survey.product_id
        return None
    finally:
        db.close()


def _rebuild_report(product_id: int):
    """The product's single consolidated report, rebuilt now with the new results."""
    try:
        from app.api.product_feedback import build_product_report

        db = SessionLocal()
        try:
            build_product_report(db, product_id)
            print(f"Analysis worker: report for product {product_id} rebuilt after re-analysis.", flush=True)
        finally:
            db.close()
    except Exception as e:
        # Not fatal: the idle check and "Refresh now" can rebuild it later.
        print(f"Analysis worker: report rebuild skipped ({e}).", flush=True)


def _swap_ids(value, old, new):
    """The result as the frontend will see it: the existing record's id, not the temporary one."""
    if isinstance(value, dict):
        return {k: (new if k in ("feedback_id", "id", "record_id") and v == old else _swap_ids(v, old, new)) for k, v in value.items()}
    if isinstance(value, list):
        return [_swap_ids(v, old, new) for v in value]
    if isinstance(value, str) and old is not None:
        return value.replace(f"/feedback/{old}/", f"/feedback/{new}/")
    return value


def run_job(job_id, path, filename, response_id, kind="analyze", target_feedback_id=None):
    started = time.time()
    print(f"Analysis worker: job {job_id} started ({filename}).", flush=True)
    stop = threading.Event()
    threading.Thread(target=_beat, args=(job_id, stop), daemon=True).start()
    try:
        # A plain event loop (not asyncio.run): Ctrl+C / reload then stops the
        # analysis at once instead of waiting for Whisper to finish.
        loop = asyncio.new_event_loop()
        try:
            # A re-analysis must not re-link the survey: the existing record keeps that link.
            result = loop.run_until_complete(
                _call_upload(path, filename, None if kind == "reanalyze" else response_id)
            )
        finally:
            loop.close()
        safe = json.loads(json.dumps(result, default=str))
        product_id = None
        if kind == "reanalyze" and target_feedback_id:
            new_id = safe.get("feedback_id")
            if not new_id:
                raise RuntimeError("The analysis didn't return a result to update.")
            product_id = apply_reanalysis(int(new_id), int(target_feedback_id))
            safe = _swap_ids(safe, new_id, int(target_feedback_id))
            safe["feedback_id"] = int(target_feedback_id)
            safe["reanalyzed"] = True
        _finish(job_id, status="done", result=safe, feedback_id=safe.get("feedback_id"), error=None)
        if product_id:
            _rebuild_report(product_id)
        print(f"Analysis worker: job {job_id} done in {time.time() - started:.0f}s.", flush=True)
        try:
            Path(path).unlink(missing_ok=True)  # the upload function keeps its own copy
        except Exception:
            pass
    except KeyboardInterrupt:
        # Stopped mid-analysis (Ctrl+C / reload): put the job straight back in the queue.
        db = SessionLocal()
        try:
            db.query(AnalysisJob).filter(AnalysisJob.id == job_id).update(
                {
                    AnalysisJob.status: "queued",
                    AnalysisJob.worker_id: None,
                    AnalysisJob.attempts: AnalysisJob.attempts - 1,  # an interruption isn't a failed attempt
                },
                synchronize_session=False,
            )
            db.commit()
            print(f"Analysis worker: job {job_id} interrupted, back in the queue.", flush=True)
        finally:
            db.close()
        raise
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e) or e.__class__.__name__
        _finish(job_id, status="failed", error=str(detail))
        print(f"Analysis worker: job {job_id} failed: {detail}", flush=True)
    finally:
        stop.set()


def _translate_when_idle():
    """Free-text answers -> English, ahead of report generation (never in the API)."""
    try:
        from app.services.consolidation import ensure_translations

        db = SessionLocal()
        try:
            n = ensure_translations(db, limit=20)
            if n:
                print(f"Analysis worker: translated {n} survey answers for reports.", flush=True)
        finally:
            db.close()
    except Exception as e:
        print(f"Analysis worker: translation skipped ({e}).", flush=True)


def _refresh_one_report() -> bool:
    """
    Keep every product's single consolidated report current: after new analyses,
    deletions, or for products that don't have one yet. One product per call, so
    queued recordings always come first. Returns True if a report was refreshed.
    """
    try:
        from app.api.product_feedback import build_product_report, stale_report_products

        db = SessionLocal()
        try:
            # Wait until no recording of that product is still queued or being analyzed,
            # so 50 uploads cause one refresh, not 50.
            busy = {
                r[0]
                for r in db.execute(
                    text(
                        "SELECT DISTINCT s.product_id FROM analysis_jobs j "
                        "JOIN survey_responses s ON s.id = j.response_id "
                        "WHERE j.status IN ('queued', 'processing')"
                    )
                ).fetchall()
            }
            for pid in stale_report_products(db):
                if pid in busy:
                    continue
                started = time.time()
                report = build_product_report(db, pid)
                print(
                    f"Analysis worker: report for product {pid} "
                    + ("refreshed" if report else "removed (no analyzed surveys)")
                    + f" in {time.time() - started:.0f}s.",
                    flush=True,
                )
                return True
        finally:
            db.close()
    except Exception as e:
        print(f"Analysis worker: report refresh skipped ({e}).", flush=True)
    return False


def worker_main(index: int = 0):
    _lower_priority()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    print(f"Analysis worker {index} ready ({worker_id}, low priority).", flush=True)
    last_translate = 0.0
    last_reports = 0.0
    while True:
        if _parent_gone():
            print(f"Analysis worker {index}: the API stopped, exiting.", flush=True)
            return
        try:
            db = SessionLocal()
            try:
                requeue_stale(db)
                job = claim_next(db, worker_id)
            finally:
                db.close()
            if job:
                run_job(*job)
                continue
            if index == 0 and time.time() - last_translate > 60:
                last_translate = time.time()
                _translate_when_idle()
            if index == 0 and time.time() - last_reports > 10:
                last_reports = time.time()
                if _refresh_one_report():
                    continue  # check the queue again before the next report
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SECONDS)