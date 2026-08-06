# This builds the AGENT (src.fast_api_app), not the public website.
#
# The site is Dockerfile.proxy (proxy.main, and the only image that bakes in
# ui/dist). `gcloud run deploy --source .` builds THIS file, so aiming it at
# the site's Cloud Run service replaces tracerlensai.com with an API that has
# none of the site's routes — it answers /health, so the deploy looks fine and
# the revision takes all the traffic. Use deploy_to_gcp.sh, which builds each
# image against the service that expects it. src/fast_api_app.py refuses to
# boot if it finds the proxy's env, so a wrong deploy fails instead of landing.

# Stage 1: Build dependencies
FROM python:3.12-slim as builder

WORKDIR /build

# Set environment variables for pip
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim

# Install system dependencies if required by causal-learn/dowhy (e.g., graphviz)
# Need to run as root before switching user
RUN apt-get update && apt-get install -y graphviz && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN groupadd -r tracerlensaiuser && useradd -r -g tracerlensaiuser tracerlensaiuser

WORKDIR /app

# Copy installed dependencies from builder
COPY --chown=tracerlensaiuser:tracerlensaiuser --from=builder /root/.local /home/tracerlensaiuser/.local
ENV PATH=/home/tracerlensaiuser/.local/bin:$PATH
ENV PYTHONPATH="/app:${PYTHONPATH:-}"

# Copy source code
COPY src/ /app/src/

# Set ownership to non-root user
RUN chown -R tracerlensaiuser:tracerlensaiuser /app

# Switch to non-root user
USER tracerlensaiuser

EXPOSE 8080

CMD ["uvicorn", "src.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
