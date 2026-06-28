import asyncio
from typing import Dict, Any


async def fetch_user_profile(user_id: str) -> Dict[str, Any]:
    """
    Mock tool to fetch a user profile from a backend service.
    """
    await asyncio.sleep(0.5)  # Simulate network latency
    return {
        "user_id": user_id,
        "name": "Alice Smith",
        "tier": "enterprise",
        "active_tickets": 1
    }


async def check_order_status(order_id: str) -> Dict[str, Any]:
    """
    Mock tool to check order status.
    """
    await asyncio.sleep(0.3)
    return {
        "order_id": order_id,
        "status": "shipped",
        "estimated_delivery": "2026-06-20"
    }

# Mapping of tool names to functions for dynamic invocation
TOOL_REGISTRY = {
    "fetch_user_profile": fetch_user_profile,
    "check_order_status": check_order_status
}
