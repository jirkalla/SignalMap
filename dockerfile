FROM python:3.14-slim

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app ./app
COPY ./alembic ./alembic
COPY alembic.ini schema_phase1.sql ./

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /code
USER appuser