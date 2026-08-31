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
  const expiredRegions = liveStatus?.health?.bedRiskExpiredRegions
    ?? data.bedRiskExpiredRegions
    ?? 0;
  const stale = liveStatus?.health?.dataStale === true;
  const analyticsStale = data.analyticsStale === true;
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

        {(stale || expiredRegions > 0) && (
          <div
            role="alert"
            style={{
              marginBottom: 14,
              padding: "10px 14px",
              border: "1px solid #fecaca",
              borderRadius: 10,
              background: "#fef2f2",
              color: "#991b1b",
              fontSize: 12.5,
              lineHeight: 1.5,
            }}
          >
            {expiredRegions > 0
              ? `병원 원천 기준시각이 만료된 ${expiredRegions}개 지역의 위험도와 해당 병상 수치를 숨겼습니다. `
              : "병상 자동 갱신이 운영 권장시간보다 지연되고 있습니다. "}
            자동 갱신이 성공하면 정상 상태로 돌아옵니다.
          </div>
        )}

        <div>
          {tab === "map" ? (
            <MapTab data={data} />
          ) : analyticsStale ? (
            <div style={{ ...pageBg, minHeight: 360, padding: 28, borderRadius: 14 }}>
              일부 지역의 병상 원천시각이 만료되어 현재 위험도 분석을 일시적으로 제공하지 않습니다.
            </div>
          ) : (
            <AnalyticsTab data={data} />
          )}
        </div>
      </div>
    </div>
  );
}
