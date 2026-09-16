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
  if (liveStatus?.error || liveStatus?.degraded) {
    return { label: "데이터 확인 필요", color: "#f59e0b" };
  }
  if (pipeline?.state === "failed") {
    return { label: "최근 갱신 실패", color: "#ef4444" };
  }
  if (stale) {
    return { label: "마지막 수집값", color: "#f59e0b" };
  }
  if (pipeline?.schedulerEnabled === false) {
    return { label: "검증 스냅샷", color: "#94a3b8" };
  }
  if (health?.status === "degraded") {
    return { label: "최근 갱신 실패", color: "#ef4444" };
  }
  if (pipeline?.state === "running") {
    return { label: pipeline.mode === "full" ? "전체 데이터 갱신 중" : "병상 데이터 갱신 중", color: "#38bdf8" };
  }
  return { label: "자동 갱신", color: "#22c55e" };
}

function formatAsOf(iso) {
  if (!iso) return null;
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return null;
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(value);
}

export default function Dashboard({ data, liveStatus = null }) {
  const [tab, setTab] = useState("map");
  const indicator = liveIndicator(liveStatus);
  const expiredRegions = liveStatus?.health?.bedRiskStaleRegions
    ?? data.bedRiskStaleRegions
    ?? liveStatus?.health?.bedRiskExpiredRegions
    ?? data.bedRiskExpiredRegions
    ?? 0;
  const expiredHospitals = liveStatus?.health?.bedRiskStaleHospitals
    ?? data.bedRiskStaleHospitals
    ?? liveStatus?.health?.bedRiskExpiredHospitals
    ?? data.bedRiskExpiredHospitals
    ?? 0;
  const stale = liveStatus?.health?.dataStale
    ?? data.lastKnownDataDisplayed
    ?? data.analyticsStale
    ?? false;
  const dataAsOf = liveStatus?.health?.scoreAsOf ?? data.kpi?.asOf;
  const asOfLabel = formatAsOf(dataAsOf);
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
              border: "1px solid #fde68a",
              borderRadius: 10,
              background: "#fffbeb",
              color: "#92400e",
              fontSize: 12.5,
              lineHeight: 1.5,
            }}
          >
            <div style={{ fontWeight: 700 }}>마지막 수집값을 표시 중입니다.</div>
            {expiredRegions > 0
              ? <>병상 원천 기준시각이 지난 <b>{expiredRegions}개 지역</b>{expiredHospitals > 0 ? <>·<b>{expiredHospitals}개 병원</b></> : null}도 값이 사라지지 않도록 마지막 성공 수집값과 계산 점수를 유지합니다. </>
              : <>자동 갱신이 운영 권장시간보다 지연되어 마지막 성공 수집값을 유지합니다. </>}
            실시간 현황과 다를 수 있으니{asOfLabel ? <> <b>{asOfLabel}</b> 기준임을 확인해 주세요.</> : " 기준시각을 확인해 주세요."}
            {" "}자동 갱신이 성공하면 최신값으로 교체됩니다.
          </div>
        )}

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
