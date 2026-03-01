"""WebSocket connection manager for broadcasting real-time run status events.

Usage:
    from app.utils.ws_manager import ws_manager

    # In a WebSocket endpoint
    await ws_manager.connect(run_id, websocket)

    # From the orchestrator or any service
    await ws_manager.broadcast(run_id, {"event": "job_status_change", ...})
"""

import logging
from typing import Dict, List

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Tracks active WebSocket subscribers per run_id and broadcasts events."""

    def __init__(self) -> None:
        # Maps run_id → list of connected WebSocket clients
        self._connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, run_id: str, websocket: WebSocket) -> None:
        """Accept and register a WebSocket connection for a run."""
        await websocket.accept()
        self._connections.setdefault(run_id, []).append(websocket)
        logger.debug("WebSocket connected for run %s (%d total)", run_id, len(self._connections[run_id]))

    def disconnect(self, run_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket from the subscriber list."""
        subscribers = self._connections.get(run_id, [])
        if websocket in subscribers:
            subscribers.remove(websocket)
        if not subscribers:
            self._connections.pop(run_id, None)
        logger.debug("WebSocket disconnected for run %s", run_id)

    async def broadcast(self, run_id: str, message: dict) -> None:
        """Send a JSON message to all subscribers of a run.

        Dead connections are silently removed.
        """
        subscribers = list(self._connections.get(run_id, []))
        dead: List[WebSocket] = []
        for ws in subscribers:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(run_id, ws)

    async def send_personal(self, websocket: WebSocket, message: dict) -> None:
        """Send a JSON message to a single WebSocket client."""
        await websocket.send_json(message)

    def subscriber_count(self, run_id: str) -> int:
        """Return how many clients are subscribed to a run."""
        return len(self._connections.get(run_id, []))


# Module-level singleton used by routes and the orchestrator
ws_manager = WebSocketManager()
