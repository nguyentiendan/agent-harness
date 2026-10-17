FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files (backend only; the frontend is built in its own image)
COPY . .

# Run as an unprivileged user (the agent's shell inherits these rights).
ARG UID=10001
ARG GID=10001
RUN groupadd -g ${GID} harness 2>/dev/null || true \
    && useradd -m -u ${UID} -g ${GID} -s /bin/bash harness \
    && chown -R ${UID}:${GID} /app
USER harness

# Expose FastAPI API Server port
EXPOSE 8000

# Default command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
