# MetrIQ single-image deployment: FastAPI serves the API and the bundled UI.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System dependencies kept minimal: psycopg2-binary and reportlab ship wheels.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --root-user-action=ignore --upgrade pip \
    && pip install --root-user-action=ignore -r backend/requirements.txt

COPY backend backend
COPY frontend frontend

# Writable paths for the local storage backend. Evidence and report artefacts
# both live under here; point STORAGE_BACKEND at supabase to keep the container
# itself stateless.
RUN mkdir -p /app/var/storage
ENV STORAGE_LOCAL_PATH=/app/var/storage \
    REPORT_STORAGE_PATH=/app/var/reports

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]