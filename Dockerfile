FROM python:3.12-slim

ARG ALIST_VERSION=v3.60.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    CINELINK_RUNTIME=docker \
    CINELINK_DATA_DIR=/app/data \
    CINELINK_PLAY_PUBLIC_URL=http://127.0.0.1:8000 \
    CINELINK_STRM_OUTPUT_DIR=/data/media \
    CINELINK_DOWNLOAD_URL_CACHE_TTL=300 \
    CINELINK_PATH_CACHE_TTL=3600 \
    CINELINK_PLAY_CHUNK_SIZE=4194304 \
    CINELINK_PLAY_LOG_REQUESTS=1 \
    CINELINK_STRM_BACKEND=play \
    CINELINK_ALIYUN_STRM_MODE=preview \
    CINELINK_QUARK_STRM_MODE=alist \
    CINELINK_ALIST_ENABLED=1 \
    CINELINK_ALIST_BIN=/usr/local/bin/alist \
    CINELINK_ALIST_DATA_DIR=/app/data/alist \
    CINELINK_ALIST_BIND_HOST=0.0.0.0 \
    CINELINK_ALIST_CHECK_HOST=127.0.0.1 \
    CINELINK_ALIST_PORT=5244 \
    CINELINK_ALIST_INTERNAL_URL=http://127.0.0.1:5244 \
    CINELINK_ALIST_PUBLIC_URL=http://127.0.0.1:5244

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata curl tar \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/alist.tar.gz "https://github.com/AlistGo/alist/releases/download/${ALIST_VERSION}/alist-linux-amd64.tar.gz" \
    && tar -xzf /tmp/alist.tar.gz -C /usr/local/bin alist \
    && chmod +x /usr/local/bin/alist \
    && rm -f /tmp/alist.tar.gz

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/data /data/media

EXPOSE 8000 5244

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/ >/dev/null || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
