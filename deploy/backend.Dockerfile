# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm

ARG MURMUR_RELEASE_SHA=""

LABEL org.opencontainers.image.title="Murmur backend" \
      org.opencontainers.image.revision="${MURMUR_RELEASE_SHA}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    PYTHON_DOTENV_DISABLED=1 \
    HOME=/home/murmur \
    MURMUR_DATA_DIR=/data \
    MURMUR_RELEASE_SHA=${MURMUR_RELEASE_SHA}

WORKDIR /app

RUN groupadd --gid 10001 murmur \
    && useradd --uid 10001 --gid murmur --create-home --home-dir /home/murmur --shell /usr/sbin/nologin murmur \
    && mkdir -p /data \
    && chown murmur:murmur /data

COPY requirements.txt ./requirements.txt
RUN python -m pip install --require-hashes --no-deps -r requirements.txt

COPY --chown=murmur:murmur backend ./backend
COPY --chown=murmur:murmur main.py ./main.py

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read()"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
