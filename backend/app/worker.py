"""
Analysis worker process.

Started automatically by the API (FMCG_WORKER_MODE=auto, the default), or by hand:
    cd backend
    python -m app.worker              # one worker
    python -m app.worker --index 1    # a second one (only worker 0 also translates)

Runs separately from the API, so analyses never slow down the web or the app.
"""

import argparse

from app import config  # noqa: F401  (loads backend/.env first, also when started by hand)
from app.database.migrations import run_migrations
from app.services.analysis_worker import worker_main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FMCG analysis worker")
    parser.add_argument("--index", type=int, default=0, help="worker number (0 also translates survey answers)")
    args = parser.parse_args()
    run_migrations()  # safe to repeat; makes sure the job table is up to date
    try:
        worker_main(args.index)
    except KeyboardInterrupt:
        # Ctrl+C in the uvicorn window, or a --reload restart: stop quietly.
        print(f"Analysis worker {args.index} stopped.", flush=True)