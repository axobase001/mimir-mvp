FROM python:3.11-slim

WORKDIR /srv

# Install system deps for bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /srv/mimir/requirements.txt
RUN pip install --no-cache-dir -r /srv/mimir/requirements.txt

COPY . /srv/mimir/

# Create data directory
RUN mkdir -p /srv/mimir/data/brains

EXPOSE 8000

ENV JWT_SECRET=""
ENV LLM_API_KEY=""
ENV BRAVE_API_KEY=""

WORKDIR /srv
CMD ["python", "-m", "mimir.main", "--config", "/srv/mimir/config.json", "--port", "8000"]
