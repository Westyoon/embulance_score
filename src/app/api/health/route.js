import { getDashboardSnapshot, readPipelineStatus } from "@/lib/dashboardSnapshot";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const snapshot = getDashboardSnapshot();
    const dataAsOf = snapshot.data.kpi.asOf;
    const timestamp = Date.parse(dataAsOf || "");
    const dataAgeMinutes = Number.isFinite(timestamp)
      ? Math.max(0, Math.round((Date.now() - timestamp) / 60_000))
      : null;
    return Response.json(
      {
        status: snapshot.degraded ? "degraded" : "ok",
        dataVersion: snapshot.version,
        dataAsOf,
        dataAgeMinutes,
        dataStale: dataAgeMinutes == null || dataAgeMinutes > 180,
        regions: snapshot.data.kpi.total,
        completeRegions: snapshot.data.kpi.complete,
        pipeline: readPipelineStatus(),
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    console.error(`Health check failed: ${error?.name ?? "Error"}`);
    return Response.json(
      { status: "unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
