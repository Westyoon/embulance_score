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

export default function Dashboard({ data }) {
  const [tab, setTab] = useState("map");
  return (
    <div style={pageBg}>
      <div style={{ maxWidth: 1180, margin: "0 auto", padding: "22px 20px 40px" }}>
        <div className="flex items-center justify-between" style={{ marginBottom: 18 }}>
          <div>
            <div style={{ fontSize: 19, fontWeight: 800 }}>응급의료 지역 위험도 모니터링</div>
            <div style={{ fontSize: 11.5, ...mutedText, marginTop: 2 }}>
              전국 시군구 지도 · region_risk_final.csv 등 실제 파이프라인 산출물 기반
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
