# Health Monitoring Service Dockerfile
# Lightweight container for running health checks independently

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (curl needed for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
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

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Run the health service
CMD ["python", "main.py"]
