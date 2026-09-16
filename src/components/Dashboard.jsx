"use client";
import { useState } from "react";
import { Map as MapIcon, LayoutDashboard } from "lucide-react";
import { pageBg, mutedText, TabGroup } from "./shared";
import MapTab from "./MapTab";
import AnalyticsTab from "./AnalyticsTab";

const TABS = [
  { key: "map", label: "메인 지도", icon: MapIcon },
  { key: "analytics", label: "분석 대시보드", icon: LayoutDashboard },
];

const KST_TIMESTAMP_FORMATTER = new Intl.DateTimeFormat("en-GB-u-nu-latn", {
  timeZone: "Asia/Seoul",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function timestampParts(iso) {
  if (!iso) return null;
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return null;
  return Object.fromEntries(
    KST_TIMESTAMP_FORMATTER.formatToParts(value)
      .filter(({ type }) => type !== "literal")
      .map(({ type, value: partValue }) => [type, partValue]),
  );
}

function latestTimestamp(...timestamps) {
  const latest = timestamps.reduce((current, timestamp) => {
    const milliseconds = new Date(timestamp).getTime();
    if (Number.isNaN(milliseconds)) return current;
    if (!current || milliseconds > current.milliseconds) {
      return { timestamp, milliseconds };
    }
    return current;
  }, null);
  return latest?.timestamp ?? null;
}

function formatUpdateTimestamp(iso) {
  const parts = timestampParts(iso);
  if (!parts) return null;
  return `${parts.month}.${parts.day}. ${parts.hour}:${parts.minute}`;
}

function liveIndicator(liveStatus, lastUpdateLabel) {
  const health = liveStatus?.health;
  const pipeline = health?.pipeline;
  const stale = health?.dataStale === true;
  if (liveStatus?.error || liveStatus?.degraded) {
    return { label: "데이터 확인 필요", color: "#f59e0b" };
  }
  if (pipeline?.state === "failed") {
    return {
      label: `최종 업데이트: ${lastUpdateLabel ?? "확인 중"}`,
      color: "#2563eb",
    };
  }
  if (stale) {
    return {
      label: `최종 업데이트: ${lastUpdateLabel ?? "확인 중"}`,
      color: "#2563eb",
    };
  }
  if (pipeline?.schedulerEnabled === false) {
    return { label: "검증 스냅샷", color: "#94a3b8" };
  }
  if (health?.status === "degraded") {
    return {
      label: `최종 업데이트: ${lastUpdateLabel ?? "확인 중"}`,
      color: "#2563eb",
    };
  }
  if (pipeline?.state === "running") {
    return { label: pipeline.mode === "full" ? "전체 데이터 갱신 중" : "병상 데이터 갱신 중", color: "#38bdf8" };
  }
  return { label: "자동 갱신", color: "#22c55e" };
}

export default function Dashboard({ data, liveStatus = null }) {
  const [tab, setTab] = useState("map");
  const pipeline = liveStatus?.health?.pipeline;
  const dataAsOf = liveStatus?.health?.scoreAsOf
    ?? liveStatus?.health?.dataAsOf
    ?? data.kpi?.asOf;
  // A failed run still advances `finishedAt`, so this represents pipeline activity,
  // while `dataAsOf` below remains the timestamp of the values currently displayed.
  const lastUpdateAt = latestTimestamp(
    pipeline?.finishedAt,
    pipeline?.lastBedsAttemptAt,
    pipeline?.lastFullAttemptAt,
    pipeline?.lastSuccessAt,
    dataAsOf,
  );
  const lastUpdateLabel = formatUpdateTimestamp(lastUpdateAt);
  const indicator = liveIndicator(liveStatus, lastUpdateLabel);
  return (
    <div style={pageBg}>
      <div style={{ maxWidth: 1180, margin: "0 auto", padding: "22px 20px 40px" }}>
        <div className="flex items-center justify-between" style={{ marginBottom: 18 }}>
          <div>
            <div style={{ fontSize: 19, fontWeight: 800 }}>응급의료 지역 위험도 모니터링</div>
            <div style={{ fontSize: 11.5, ...mutedText, marginTop: 2 }}>
              전국 시군구 지도 · 실제 운영 파이프라인 산출물 기반
              {liveStatus && (
                <span style={{ marginLeft: 8, color: indicator.color }}>
                  ● {indicator.label}
                </span>
              )}
            </div>
          </div>
          <TabGroup options={TABS} active={tab} onChange={setTab} />
        </div>

        <div>
          {tab === "map" ? (
            <MapTab data={data} />
          ) : (
            <AnalyticsTab data={data} />
          )}
        </div>
      </div>
    </div>
  );
}
