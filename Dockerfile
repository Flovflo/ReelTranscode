FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        mediainfo \
        tesseract-ocr \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY reeltranscode /app/reeltranscode
COPY docker/reeltranscode.docker.yaml /opt/reeltranscode/examples/reeltranscode.docker.yaml

RUN python -m pip install --upgrade pip \
    && python -m pip install .

ENTRYPOINT ["tini", "--", "reeltranscode"]
CMD ["--help"]
