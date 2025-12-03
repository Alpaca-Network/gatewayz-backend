# Health Monitoring Service Dockerfile
# Lightweight container for running health checks independently

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy health-service requirements first for caching
COPY health-service/requirements.txt /app/health-service/requirements.txt

# Install Python dependencies from health-service specific requirements
# This keeps the image lightweight by only installing what's needed
RUN pip install --no-cache-dir -r /app/health-service/requirements.txt

# Copy the entire project (needed for shared modules)
COPY . /app

# Set Python path to include src
ENV PYTHONPATH="${PYTHONPATH}:/app:/app/src"

# Set working directory to health-service
WORKDIR /app/health-service

# Default port (Railway will override with $PORT)
ENV PORT=8001

# Note: Railway handles healthchecks externally via railway.json config
# Docker HEALTHCHECK removed to avoid port mismatch issues

# Run the health service
CMD ["python", "main.py"]
