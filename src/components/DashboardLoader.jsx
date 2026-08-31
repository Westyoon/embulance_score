"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import Dashboard from "./Dashboard";
import { mutedText, pageBg } from "./shared";

const POLL_SECONDS = Math.max(
  15,
  Number(process.env.NEXT_PUBLIC_DASHBOARD_POLL_SECONDS) || 60,
);

function LoadingPanel({ error, onRetry }) {
  return (
    <main style={{ ...pageBg, minHeight: "100vh", padding: 24 }}>
      <div
        style={{
          maxWidth: 560,
          margin: "18vh auto 0",
          padding: 28,
          border: "1px solid rgba(148, 163, 184, 0.18)",
          borderRadius: 18,
          background: "rgba(15, 23, 42, 0.72)",
          textAlign: "center",
        }}
        aria-live="polite"
      >
        <div style={{ fontSize: 18, fontWeight: 800 }}>
          {error ? "운영 데이터를 불러오지 못했습니다" : "최신 운영 데이터를 불러오는 중입니다"}
        </div>
        <div style={{ marginTop: 9, fontSize: 13, ...mutedText }}>
          {error || "병상·접근성·의료진 스냅샷을 확인하고 있습니다."}
        </div>
        {error && (
          <button
            type="button"
            onClick={onRetry}
            style={{
              marginTop: 18,
              padding: "9px 16px",
              border: 0,
              borderRadius: 10,
              background: "#2563eb",
              color: "white",
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            다시 시도
          </button>
        )}
      </div>
    </main>
  );
}

export default function DashboardLoader() {
  const [snapshot, setSnapshot] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const etagRef = useRef(null);
  const requestRef = useRef(null);

  const refresh = useCallback(async () => {
    if (requestRef.current) return;
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const headers = etagRef.current ? { "If-None-Match": etagRef.current } : {};
      const healthRequest = fetch("/api/health", {
        cache: "no-store",
        signal: controller.signal,
      }).catch(() => null);
      const response = await fetch("/api/dashboard", {
        cache: "no-store",
        headers,
        signal: controller.signal,
      });
      const healthResponse = await healthRequest;
      if (healthResponse?.ok) setHealth(await healthResponse.json());
      if (response.status === 304) {
        setError(null);
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (!payload?.data || !payload?.version) throw new Error("invalid snapshot");
      etagRef.current = response.headers.get("etag") || `"${payload.version}"`;
      setSnapshot(payload);
      setError(null);
    } catch (reason) {
      if (reason?.name !== "AbortError") {
        setError("잠시 후 자동으로 다시 시도합니다.");
      }
    } finally {
      requestRef.current = null;
    }
  }, []);

  useEffect(() => {
    const initialTimer = window.setTimeout(refresh, 0);
    const timer = window.setInterval(refresh, POLL_SECONDS * 1000);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      requestRef.current?.abort();
    };
  }, [refresh]);

  if (!snapshot) return <LoadingPanel error={error} onRetry={refresh} />;
  return (
    <Dashboard
      data={snapshot.data}
      liveStatus={{
        version: snapshot.version,
        degraded: snapshot.degraded,
        error,
        health,
      }}
    />
  );
}
