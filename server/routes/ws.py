import logging
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from ...server.auth.jwt import verify_token

log = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        # user_id -> list of WebSocket connections
        self._connections: dict[str, list[WebSocket]] = defaultdict(list)
        # For backward compat: anonymous connections
        self._anonymous: list[WebSocket] = []

    async def connect(self, ws: WebSocket, user_id: str = None) -> None:
        await ws.accept()
        if user_id:
            self._connections[user_id].append(ws)
            log.info("WebSocket connected for user %s (%d total for user)",
                     user_id, len(self._connections[user_id]))
        else:
            self._anonymous.append(ws)
            log.info("Anonymous WebSocket connected")

    def disconnect(self, ws: WebSocket, user_id: str = None) -> None:
        if user_id and user_id in self._connections:
            if ws in self._connections[user_id]:
                self._connections[user_id].remove(ws)
            if not self._connections[user_id]:
                del self._connections[user_id]
            log.info("WebSocket disconnected for user %s", user_id)
        elif ws in self._anonymous:
            self._anonymous.remove(ws)

    async def broadcast_to_user(self, user_id: str, message: dict) -> None:
        """Send a message to all connections for a specific user."""
        if user_id not in self._connections:
            return
        dead: list[WebSocket] = []
        for ws in self._connections[user_id]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, user_id)

    async def broadcast(self, message: dict) -> None:
        """Broadcast to all anonymous connections (backward compat)."""
        dead: list[WebSocket] = []
        for ws in self._anonymous:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(default=None)) -> None:
    user_id = None
    if token:
        user_id = verify_token(token)

    await manager.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
