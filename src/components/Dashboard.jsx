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

function liveIndicator(liveStatus) {
  const health = liveStatus?.health;
  const pipeline = health?.pipeline;
  const stale = health?.dataStale === true;
  if (liveStatus?.error || liveStatus?.degraded || health?.status === "degraded") {
    return { label: "데이터 확인 필요", color: "#f59e0b" };
  }
  if (pipeline?.schedulerEnabled === false) {
    return { label: "검증 스냅샷", color: "#94a3b8" };
  }
  if (pipeline?.state === "failed" || stale) {
    return { label: stale ? "데이터 갱신 지연" : "최근 갱신 실패", color: "#ef4444" };
  }
  if (pipeline?.state === "running") {
    return { label: pipeline.mode === "full" ? "전체 데이터 갱신 중" : "병상 데이터 갱신 중", color: "#38bdf8" };
  }
  return { label: "자동 갱신", color: "#22c55e" };
}

export default function Dashboard({ data, liveStatus = null }) {
  const [tab, setTab] = useState("map");
  const indicator = liveIndicator(liveStatus);
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

        <div style={{ height: tab === "map" ? 680 : "auto" }}>
          {tab === "map" ? <MapTab data={data} /> : <AnalyticsTab data={data} />}
        </div>
      </div>
    </div>
  );
}
