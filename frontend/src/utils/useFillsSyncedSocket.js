import { useEffect, useRef } from "react";
import { WS_BASE_URL } from "../api.js";

// Subscribes to the backend's fills_synced push (see backend/routes/ws.py) —
// fired the moment the 20-minute background fill-sync scheduler completes,
// so a page can refetch itself immediately instead of only updating on a
// manual reload or a fixed poll interval. Reconnects automatically if the
// socket drops (laptop sleep, network blip, backend restart), since the
// scheduler keeps running server-side regardless of whether anyone's
// currently connected to hear about it.
export function useFillsSyncedSocket(onFillsSynced) {
  const callbackRef = useRef(onFillsSynced);
  callbackRef.current = onFillsSynced;

  useEffect(() => {
    let socket;
    let reconnectTimeout;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;

      socket = new WebSocket(`${WS_BASE_URL}/ws/updates`);

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === "fills_synced") callbackRef.current?.(message.last_synced_at);
        } catch {
          // Ignore malformed/unrecognized messages rather than crash the socket.
        }
      };

      socket.onclose = () => {
        if (!cancelled) reconnectTimeout = setTimeout(connect, 3000);
      };

      socket.onerror = () => socket.close();
    };

    connect();

    return () => {
      cancelled = true;
      clearTimeout(reconnectTimeout);
      socket?.close();
    };
  }, []);
}
