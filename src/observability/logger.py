import os
import json
import logging

try:
    from google.cloud import logging as gcp_logging
    HAVE_GCP_LOGGING = True
except ImportError:
    HAVE_GCP_LOGGING = False

def setup_logger():
    """
    Configures standard Python logging to output JSON structured logs
    when running in GKE, or standard output for local dev.
    """
    logger = logging.getLogger("agent_orchestrator")
    logger.setLevel(logging.INFO)

    # Check if we are running in GCP
    if os.environ.get("KUBERNETES_SERVICE_HOST") and HAVE_GCP_LOGGING:
        client = gcp_logging.Client()
        client.setup_logging()
    else:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        if not logger.handlers:
            logger.addHandler(handler)

    return logger


def log_agent_metrics(user_id: str, prompt: str, metrics: dict):
    """
    Logs structured data intended for BigQuery ingestion via a Log Sink.
    """
    logger = logging.getLogger("agent_orchestrator")

    log_entry = {
        "event_type": "agent_invocation",
        "user_id": user_id,
        "prompt_length": len(prompt),
        "model": metrics.get("model"),
        "provider": metrics.get("provider"),
        "latency_ms": metrics.get("latency_ms"),
        "prompt_tokens": metrics.get("prompt_tokens"),
        "completion_tokens": metrics.get("completion_tokens"),
        "escalated": metrics.get("escalated", False)
    }

    logger.info(json.dumps(log_entry))
