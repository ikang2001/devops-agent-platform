# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
ARG UV_VERSION=0.11.31
RUN pip install --upgrade pip \
    && pip install "uv==${UV_VERSION}"

# uv 仅用于构建；先装到 Builder 全局环境，避免把它及构建依赖复制进运行时镜像。
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml uv.lock README.md ./
RUN uv export --locked --no-dev --no-emit-project --no-hashes \
       --output-file /tmp/requirements.txt \
    && pip install --requirement /tmp/requirements.txt

COPY alembic.ini main.py ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-deps .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    DEVOPS_AGENT_APP_ENV=production

RUN addgroup --system --gid 10001 devops-agent \
    && adduser --system --uid 10001 --ingroup devops-agent \
       --home /app devops-agent

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini main.py ./
COPY migrations ./migrations

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read()"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
