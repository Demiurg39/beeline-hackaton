FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

COPY src ./src
COPY data ./data
COPY main.py recommend.py ./

EXPOSE 8000

CMD ["uv", "run", "python", "main.py", "serve", "--host", "0.0.0.0", "--port", "8000"]
