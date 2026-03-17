FROM python:3.11-slim

WORKDIR /app

# Install system deps for bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory
RUN mkdir -p data/brains

EXPOSE 8000

ENV JWT_SECRET=""
ENV LLM_API_KEY=""
ENV BRAVE_API_KEY=""

CMD ["python", "-m", "mimir.main", "--config", "config.json", "--port", "8000"]
