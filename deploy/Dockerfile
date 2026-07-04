# Stage 1: Build dependencies
FROM python:3.11-slim as builder

WORKDIR /build

# Set environment variables for pip
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

# Create a non-root user
RUN groupadd -r agentuser && useradd -r -g agentuser agentuser

WORKDIR /app

# Copy installed dependencies from builder
COPY --chown=agentuser:agentuser --from=builder /root/.local /home/agentuser/.local
ENV PATH=/home/agentuser/.local/bin:$PATH

# Copy source code
COPY src/ /app/src/

# Set ownership to non-root user
RUN chown -R agentuser:agentuser /app

# Switch to non-root user
USER agentuser

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
