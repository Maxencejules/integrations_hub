FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# psycopg2 for Alembic sync migrations
RUN pip install --no-cache-dir psycopg2-binary

COPY alembic.ini .
COPY migrations/ migrations/

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn integrations_hub.main:app --host 0.0.0.0 --port 8000"]
