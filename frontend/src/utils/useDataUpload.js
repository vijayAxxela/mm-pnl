import { useEffect, useRef, useState } from "react";
import { api, WS_BASE_URL } from "../api.js";

// Own socket connection, same pattern as useLossAlertSound — simpler than
// sharing one socket across every feature that listens on /ws/updates, at
// the cost of one extra open connection for the life of the tab (this hook
// is mounted once, globally, in App.jsx).
function useUploadProgressSocket(jobId, onMessage) {
  const callbackRef = useRef(onMessage);
  callbackRef.current = onMessage;

  useEffect(() => {
    if (!jobId) return undefined;

    let socket;
    let reconnectTimeout;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      socket = new WebSocket(`${WS_BASE_URL}/ws/updates`);

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === "data_upload_progress" && message.job_id === jobId) {
            callbackRef.current?.(message);
          }
        } catch {
          // Ignore malformed/unrecognized messages.
        }
      };

      socket.onclose = () => {
        if (!cancelled) reconnectTimeout = setTimeout(connect, 2000);
      };

      socket.onerror = () => socket.close();
    };

    connect();

    return () => {
      cancelled = true;
      clearTimeout(reconnectTimeout);
      socket?.close();
    };
  }, [jobId]);
}

// Single instance, owned by App.jsx, feeding the topbar trigger button, the
// upload modal, and the progress banner (rendered in normal document flow
// right below the topbar so it pushes page content down instead of
// overlaying it — see App.jsx) — all three need the same job/progress
// state, so it lives here instead of duplicated per-component.
export function useDataUpload() {
  const [modalOpen, setModalOpen] = useState(false);
  const [step, setStep] = useState(1);
  const [tsFile, setTsFile] = useState(null);
  const [ohlcFile, setOhlcFile] = useState(null);
  const [ohlcContract, setOhlcContract] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState(null);

  const [jobId, setJobId] = useState(null);
  const [progress, setProgress] = useState(null);
  const dismissTimeoutRef = useRef(null);

  const dismissProgress = () => {
    clearTimeout(dismissTimeoutRef.current);
    setJobId(null);
    setProgress(null);
  };

  useUploadProgressSocket(jobId, (message) => {
    setProgress(message);
    if (message.done) {
      dismissTimeoutRef.current = setTimeout(dismissProgress, 10000);
    }
  });

  useEffect(() => () => clearTimeout(dismissTimeoutRef.current), []);

  const openModal = () => {
    setStep(1);
    setTsFile(null);
    setOhlcFile(null);
    setOhlcContract("");
    setFormError(null);
    setModalOpen(true);
  };

  const closeModal = () => {
    if (!submitting) setModalOpen(false);
  };

  const handleSubmit = async () => {
    if (!tsFile && !ohlcFile) {
      setFormError("Choose at least one file to upload.");
      return;
    }
    setFormError(null);
    setSubmitting(true);
    try {
      const formData = new FormData();
      if (tsFile) formData.append("time_sales_file", tsFile);
      if (ohlcFile) formData.append("ohlc_file", ohlcFile);
      if (ohlcContract.trim()) formData.append("ohlc_contract", ohlcContract.trim());

      const result = await api.uploadData(formData);
      setModalOpen(false);
      clearTimeout(dismissTimeoutRef.current);
      setProgress({ stage: "reading", message: "Starting upload...", percent: 0, done: false });
      setJobId(result.job_id);
    } catch (e) {
      setFormError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  return {
    modalOpen,
    step,
    setStep,
    tsFile,
    setTsFile,
    ohlcFile,
    setOhlcFile,
    ohlcContract,
    setOhlcContract,
    submitting,
    formError,
    openModal,
    closeModal,
    handleSubmit,
    progress,
    dismissProgress,
  };
}
