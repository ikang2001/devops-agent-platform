# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

ARG UV_VERSION=0.11.31
COPY pyproject.toml uv.lock README.md ./
RUN python -m pip install --upgrade pip \
    && python -m pip install "uv==${UV_VERSION}" \
    && uv export --locked --no-dev --no-emit-project --no-hashes \
       --output-file /tmp/requirements.txt \
    && python -m pip install --requirement /tmp/requirements.txt

COPY app ./app
COPY scenarios ./scenarios
COPY runbooks ./runbooks

RUN python -m pip install --no-deps . \
    && addgroup --system --gid 10001 minishop \
    && adduser --system --uid 10001 --ingroup minishop --home /app minishop \
    && mkdir -p /var/log/minishop \
    && chown -R 10001:10001 /app /var/log/minishop

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
