# Production image - runs on Render, Railway, Fly.io, Cloud Run, Hugging Face Spaces, etc.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000 \
    REMBG_HOME=/app/.rembg NUMBA_CACHE_DIR=/tmp/numba-cache
WORKDIR /app

# onnxruntime needs the OpenMP runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Bake the background-removal model into the image so the first request is fast.
RUN python -c "from rembg import new_session; new_session('u2netp')"

COPY app ./app
COPY static ./static

RUN useradd --create-home pixelpal && chown -R pixelpal /app
USER pixelpal

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health')"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
