# routes/ws.py
"""
Minimal WebSocket push channel. The frontend subscribes once and receives a
'fills_synced' message whenever the background fill-sync scheduler (see
main._run_scheduled_fill_sync) completes — carrying the same timestamp
LastSyncedBadge already displays — so pages like the PNL overview can
refetch themselves immediately instead of only updating on a manual reload.
"""
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)

# Every currently-connected client. Module-level and in-memory is enough
# here — this is a single-process app, and a dropped/restarted server just
# means clients reconnect (see the frontend's retry loop) and pick the next
# scheduled sync's broadcast up normally.
_connections: set[WebSocket] = set()


@router.websocket("/updates")
async def updates_socket(websocket: WebSocket):
    await websocket.accept()
    _connections.add(websocket)
    try:
        while True:
            # Clients don't send anything meaningful; this just blocks until
            # the socket closes, which is how a disconnect gets detected.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WebSocket connection error: {e}")
    finally:
        _connections.discard(websocket)


async def broadcast(message: dict):
    """
    Send a JSON message to every currently-connected client. A send failure
    on one stale/half-closed connection shouldn't stop the others from
    getting the update, so dead sockets are just dropped from the set.
    """
    dead = []
    for connection in _connections:
        try:
            await connection.send_json(message)
        except Exception:
            dead.append(connection)
    for connection in dead:
        _connections.discard(connection)
