FROM python:3.13-slim

WORKDIR /app

# System deps required by psycopg2
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first — Docker caches this layer separately
# If only your code changes, pip install is skipped on rebuild
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Production command — docker-compose overrides this with --reload in dev
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]