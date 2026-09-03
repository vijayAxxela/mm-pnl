import { useEffect, useRef } from "react";
import { WS_BASE_URL } from "../api.js";

// Three short beeps generated via the Web Audio API — no audio file asset
// to manage/host. Browsers block audio until the page has had at least one
// user interaction (click/keypress); on a dashboard someone's actively
// using, that's already true by the time a real alert could fire.
function playAlertBeeps() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    const beep = (startTime) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.25, startTime);
      gain.gain.exponentialRampToValueAtTime(0.001, startTime + 0.25);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(startTime);
      osc.stop(startTime + 0.25);
    };
    const now = ctx.currentTime;
    beep(now);
    beep(now + 0.3);
    beep(now + 0.6);
  } catch {
    // Audio blocked/unsupported — nothing more to do about it here.
  }
}

// Mounted once, globally (see App.jsx), so a loss alert fires regardless of
// which page is open. Separate connection from useFillsSyncedSocket
// (PnlPage-only) — simpler than sharing one socket across the app, at the
// cost of a second open connection while the PNL page is up.
export function useLossAlertSound(onAlert) {
  const callbackRef = useRef(onAlert);
  callbackRef.current = onAlert;

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
          if (message.type === "loss_alert") {
            playAlertBeeps();
            callbackRef.current?.(message);
          }
        } catch {
          // Ignore malformed/unrecognized messages.
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
