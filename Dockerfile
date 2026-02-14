FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY alembic.ini .
COPY migrations/ migrations/
COPY src/ src/

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn integrations_hub.main:app --host 0.0.0.0 --port 8000"]
