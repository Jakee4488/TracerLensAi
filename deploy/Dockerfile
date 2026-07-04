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
RUN groupadd -r tracelensuser && useradd -r -g tracelensuser tracelensuser

WORKDIR /app

# Copy installed dependencies from builder
COPY --chown=tracelensuser:tracelensuser --from=builder /root/.local /home/tracelensuser/.local
ENV PATH=/home/tracelensuser/.local/bin:$PATH

# Copy source code
COPY src/ /app/src/

# Set ownership to non-root user
RUN chown -R tracelensuser:tracelensuser /app

# Switch to non-root user
USER tracelensuser

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
