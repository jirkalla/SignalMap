FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app ./app
COPY ./alembic ./alembic
COPY ./scripts ./scripts
COPY alembic.ini schema_phase1.sql ./

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /code
USER appuser

# One process keeps the small-server footprint low. The app is only reachable
# through Caddy on the private Compose network, which supplies proxy headers.
# Failed migrations prevent the web server from starting.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
