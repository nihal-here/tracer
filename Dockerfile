FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Environment defaults
ENV PORT=8000
ENV MAX_CONCURRENT_INVESTIGATIONS=2
ENV RATE_LIMIT_PER_MIN=5

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
