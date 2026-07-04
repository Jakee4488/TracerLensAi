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
RUN groupadd -r tracerlensaiuser && useradd -r -g tracerlensaiuser tracerlensaiuser

WORKDIR /app

# Copy installed dependencies from builder
COPY --chown=tracerlensaiuser:tracerlensaiuser --from=builder /root/.local /home/tracerlensaiuser/.local
ENV PATH=/home/tracerlensaiuser/.local/bin:$PATH

# Copy source code
COPY src/ /app/src/

# Set ownership to non-root user
RUN chown -R tracerlensaiuser:tracerlensaiuser /app

# Switch to non-root user
USER tracerlensaiuser

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
