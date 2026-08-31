import { getDashboardSnapshot } from "@/lib/dashboardSnapshot";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CACHE_CONTROL = "no-cache, must-revalidate";

export async function GET(request) {
  try {
    const snapshot = getDashboardSnapshot();
    const etag = `"${snapshot.version}"`;
    if (request.headers.get("if-none-match") === etag) {
      return new Response(null, {
        status: 304,
        headers: {
          "Cache-Control": CACHE_CONTROL,
          ETag: etag,
          "X-Data-Version": snapshot.version,
        },
      });
    }
    return Response.json(snapshot, {
      headers: {
        "Cache-Control": CACHE_CONTROL,
        ETag: etag,
        "X-Data-Version": snapshot.version,
      },
    });
  } catch (error) {
    console.error(`Dashboard snapshot failed: ${error?.name ?? "Error"}`);
    return Response.json(
      { error: "대시보드 데이터를 불러오지 못했습니다." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
