"""WebSocket hub: authenticated /ws endpoint with stable JSON events."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from blaze_hammer.web.auth import SESSION_COOKIE
from blaze_hammer.web.dependencies import authenticate_websocket, get_ws_state

router = APIRouter()

#: Client queue depth; overflow drops the newest events (stats ticker keeps
#: the dashboard correct regardless).
QUEUE_DEPTH = 500

CLOSE_UNAUTHENTICATED = 4401
CLOSE_EXPIRED = 4402


class Hub:
    """Fan-out bus from run lifecycle to every connected client."""

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self.dropped = 0

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_DEPTH)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._clients.discard(queue)

    def client_count(self) -> int:
        return len(self._clients)

    async def publish(self, event: dict[str, Any]) -> None:
        for queue in tuple(self._clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped += 1


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    state = get_ws_state(ws)
    if not authenticate_websocket(ws, state):
        await ws.close(code=CLOSE_UNAUTHENTICATED)
        return
    await ws.accept()

    token = ws.cookies.get(SESSION_COOKIE) if state.settings.auth.enabled else None
    sessions = state.sessions
    manager = state.manager
    assert manager is not None
    hello_session = sessions.get(token)
    await ws.send_json(
        {
            "type": "hello",
            "username": hello_session.username if hello_session else "local",
            "runs": manager.list_summaries(),
        }
    )

    queue = state.hub.subscribe()

    async def pump() -> None:
        while True:
            event = await queue.get()
            await ws.send_json(event)

    sender = asyncio.create_task(pump())
    try:
        while True:
            # Idle timeout doubles as a periodic session re-validation.
            raw = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
            if raw == "ping":
                await ws.send_json({"type": "pong"})
            if state.settings.auth.enabled and sessions.get(token) is None:
                await ws.send_json({"type": "auth.expired"})
                await ws.close(code=CLOSE_EXPIRED)
                break
    except (TimeoutError, WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
        state.hub.unsubscribe(queue)


def cookie_name() -> str:
    return SESSION_COOKIE
