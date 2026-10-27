FROM mirror.gcr.io/library/python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
COPY server.py ./
COPY data/ ./data/

RUN uv sync --frozen --no-dev

EXPOSE 8002

CMD ["uv", "run", "--frozen", "--no-sync", "python", "server.py"]
