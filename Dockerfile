# 3D ワーカー（worker/）のイメージ。Cloud Run で動かす。設計は docs/worker.md
# Docker Hub のレート制限に当たるときは --build-arg BASE_IMAGE=mirror.gcr.io/library/python:3.12-slim
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    WORKER_WORK_ROOT=/tmp/3d-worker \
    WORKER_SANDBOX_USER=sandbox

# git: スレッドごとの作業場所（ブランチ）/ libgl1・libglib2.0-0: OCP（build123d）が読み込むライブラリ
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --no-create-home --shell /usr/sbin/nologin sandbox

WORKDIR /app
RUN pip install --no-cache-dir uv==0.9.2

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group worker --no-install-project

# ツールと共通ライブラリはイメージのものを使う（作業場所からは models/ だけを扱う）
COPY CLAUDE.md README.md ./
COPY printlib ./printlib
COPY tools ./tools
COPY worker ./worker
COPY templates ./templates

EXPOSE 8080
# ワーカーは root で動き、Claude が書いたコードだけを sandbox ユーザーで実行する（worker/sandbox.py）
CMD ["sh", "-c", "uvicorn worker.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
