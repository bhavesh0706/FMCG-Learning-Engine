# Backend + AI Model Service in one container (two processes, ports 8000 and 8001).
# Build context: the project root (docker-compose.yml does this).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KMP_DUPLICATE_LIB_OK=TRUE

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

# Layout inside the image:
#   /srv/backend      backend API (recordings -> /srv/uploads/videos, queue -> /srv/backend/uploads/queue)
#   /srv/ai_service   AI service  (models -> /srv/ai_service/models, temp files -> /srv/ai_service/work)
WORKDIR /srv

COPY backend/requirements-backend.txt backend/
COPY ai_service/requirements.txt ai_service/requirements-translation.txt ai_service/
RUN pip install -r backend/requirements-backend.txt -r ai_service/requirements.txt

# Translation models (IndicTrans2 / NLLB). Build with --build-arg INSTALL_TRANSLATION=0 to skip.
ARG INSTALL_TRANSLATION=1
RUN if [ "$INSTALL_TRANSLATION" = "1" ]; then \
      pip install torch --index-url https://download.pytorch.org/whl/cpu \
      && pip install -r ai_service/requirements-translation.txt; \
    fi

COPY backend/app backend/app
COPY backend/run.py backend/run.py
COPY ai_service ai_service
RUN rm -f ai_service/models/deep-filter.exe
COPY docker/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh && mkdir -p /srv/uploads /srv/backend/uploads /srv/ai_service/work

EXPOSE 8000 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
  CMD curl -fs http://127.0.0.1:8000/health || exit 1
CMD ["start.sh"]
