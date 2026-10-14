#!/bin/sh
# One container, two processes on two ports:
#   AI Model Service  -> 8001  (/srv/ai_service: Whisper + Qwen)
#   Backend API       -> 8000  (/srv/backend: calls the AI service over HTTP)
# If either one stops, the container stops, so Docker restarts it.

cd /srv/ai_service
uvicorn main:app --host 0.0.0.0 --port "${AI_PORT:-8001}" --workers "${AI_WORKERS:-1}" &
AI_PID=$!

echo "Waiting for the AI service (loads Whisper on start)..."
i=0
until curl -fs "http://127.0.0.1:${AI_PORT:-8001}/health" > /dev/null; do
  i=$((i + 1))
  if [ $i -gt 600 ] || ! kill -0 $AI_PID 2>/dev/null; then
    echo "AI service didn't start."; exit 1
  fi
  sleep 2
done
echo "AI service ready."

cd /srv/backend
# Host, port and CORS come from BACKEND_HOST / BACKEND_PORT / CORS_ORIGINS (docker-compose.yml)
python run.py &
API_PID=$!

trap 'kill $AI_PID $API_PID 2>/dev/null' TERM INT
while kill -0 $AI_PID 2>/dev/null && kill -0 $API_PID 2>/dev/null; do sleep 5; done
echo "A service stopped; shutting the container down."
kill $AI_PID $API_PID 2>/dev/null || true
exit 1
