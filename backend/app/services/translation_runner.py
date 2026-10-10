"""
Translation in its own helper process, isolated from Whisper.

Loading PyTorch + a translation model inside the analysis worker can crash it on
Windows (OpenMP clash), run it out of memory, or stall on a first-time model
download. Here the model lives in a separate helper process instead:

  - it starts once and is reused, so the model loads only once
  - if it crashes or doesn't answer in time, this recording falls back to
    Whisper's own translation (the analysis always completes)
  - after a failure, translation pauses for a while so the next recordings
    don't each wait for a timeout, then it is tried again automatically

Settings (before starting uvicorn):
  FMCG_TRANSLATE_TIMEOUT   seconds to wait for one recording's translation (default 240;
                           the very first one may include the model download)
  FMCG_TRANSLATE_ISOLATED  "0" to translate inside the worker instead (not recommended)
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
TIMEOUT = float(os.environ.get("FMCG_TRANSLATE_TIMEOUT", "240") or 240)
PAUSE_AFTER_FAILURE = 10 * 60
ISOLATED = os.environ.get("FMCG_TRANSLATE_ISOLATED", "1").strip() != "0"

_lock = threading.Lock()
_proc = None
_answers: "queue.Queue[str]" = queue.Queue()
_paused_until = 0.0
_next_id = 0


def _reader(proc):
    for line in proc.stdout:
        _answers.put(line)


def _start():
    global _proc, _answers
    _answers = queue.Queue()
    env = dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    _proc = subprocess.Popen(
        [sys.executable, "-m", "app.services.translation_service", "--serve"],
        cwd=str(BACKEND_DIR),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,  # the helper's log lines show in the same console
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=env,
    )
    threading.Thread(target=_reader, args=(_proc,), daemon=True).start()
    print("Translation helper started (separate process).", flush=True)


def _stop():
    global _proc
    if _proc is not None:
        try:
            _proc.kill()
        except Exception:
            pass
    _proc = None


def translate_segments(segments, lang):
    """Same contract as translation_service.translate_segments: (segments, engine) or None."""
    global _paused_until, _next_id
    if not ISOLATED:
        from app.services.translation_service import translate_segments as direct

        return direct(segments, lang)
    if time.time() < _paused_until:
        print("Translation paused after a recent failure; using Whisper's translation.", flush=True)
        return None

    with _lock:
        try:
            if _proc is None or _proc.poll() is not None:
                if _proc is not None:
                    print(f"Translation helper had stopped (exit code {_proc.returncode}); restarting.", flush=True)
                _start()
            _next_id += 1
            rid = _next_id
            clean = [
                {k: s[k] for k in ("start", "end", "text", "avg_logprob", "no_speech_prob", "compression_ratio") if s.get(k) is not None}
                for s in segments
            ]
            _proc.stdin.write(json.dumps({"id": rid, "lang": lang, "segments": clean}, ensure_ascii=False) + "\n")
            _proc.stdin.flush()

            deadline = time.time() + TIMEOUT
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"no answer within {TIMEOUT:.0f}s")
                try:
                    line = _answers.get(timeout=min(remaining, 2))
                except queue.Empty:
                    if _proc.poll() is not None:
                        raise RuntimeError(f"the helper stopped (exit code {_proc.returncode})")
                    continue
                answer = json.loads(line)
                if answer.get("id") not in (rid, None):
                    continue  # a late answer to an earlier, timed-out request
                if answer.get("ok"):
                    return answer["segments"], answer["engine"]
                print(f"Translation helper: {answer.get('error')}; using Whisper's translation.", flush=True)
                return None
        except Exception as e:
            crashed = _proc is None or _proc.poll() is not None
            print(
                f"Translation failed ({e}); using Whisper's translation for this recording. "
                f"Trying the translation model again in {PAUSE_AFTER_FAILURE // 60} minutes.",
                flush=True,
            )
            if crashed:
                _stop()
            # A timeout may just be a first-time model download: keep the helper running.
            _paused_until = time.time() + PAUSE_AFTER_FAILURE
            return None