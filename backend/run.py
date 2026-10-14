"""
Start the backend API on the host and port from backend/.env.

    cd backend
    python run.py

Settings: BACKEND_HOST, BACKEND_PORT, BACKEND_RELOAD (see .env.example).
If the port is already in use, this stops with a clear message instead of
starting on a different port.
"""

import os
import socket
import sys

import uvicorn

from app import config


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def main() -> int:
    # Run from the backend folder, so "app.main" is found from any starting folder.
    os.chdir(config.BACKEND_DIR)
    host, port = config.BACKEND_HOST, config.BACKEND_PORT

    if not port_is_free(host, port):
        print(
            f"ERROR: port {port} on {host} is already in use.\n"
            f"Stop the program using it, or change BACKEND_PORT in backend/.env "
            f"(and VITE_API_BASE_URL in frontend/.env.local to match).",
            file=sys.stderr,
        )
        return 1

    print(f"Backend ({config.APP_ENV}) starting on http://{host}:{port}")
    print(f"Allowed frontend origins: {', '.join(config.CORS_ORIGINS)}")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=config.BACKEND_RELOAD,
        # Only watch the code, not uploaded recordings or the analysis queue.
        reload_dirs=[str(config.BACKEND_DIR / "app")] if config.BACKEND_RELOAD else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
