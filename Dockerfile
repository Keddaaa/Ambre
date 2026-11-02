# Image du worker AMBRE (sync + cycle papier). Aucun secret n'est copié dans l'image.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.13.6
WORKDIR /app
COPY pyproject.toml uv.lock alembic.ini ./
COPY migrations ./migrations
COPY configs ./configs
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
CMD ["ambre", "worker"]
