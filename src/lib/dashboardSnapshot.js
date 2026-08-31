import "server-only";

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { BOUNDARY_FILE, DATA_DIR } from "./csvServer";
import { DASHBOARD_SOURCE_FILES, loadDashboardData } from "./loadDashboardData";

const CACHE_KEY = Symbol.for("embulance-score.dashboard-snapshot");
const PIPELINE_STATE_DIR = process.env.PIPELINE_STATE_DIR
  ? path.resolve(process.env.PIPELINE_STATE_DIR)
  : path.join(process.cwd(), "runtime", "state");
const PIPELINE_STATUS_FILE = path.join(PIPELINE_STATE_DIR, "pipeline_status.json");

function sourceState(filePath) {
  try {
    const stat = fs.statSync(filePath);
    return `${filePath}:${stat.size}:${stat.mtimeMs}`;
  } catch (error) {
    if (error?.code === "ENOENT") return `${filePath}:missing`;
    throw error;
  }
}

export function dashboardVersion() {
  const sourcePaths = DASHBOARD_SOURCE_FILES.map((filename) => (
    path.join(DATA_DIR, filename)
  ));
  sourcePaths.push(BOUNDARY_FILE);
  const state = sourcePaths.map(sourceState).join("\n");
  return crypto.createHash("sha256").update(state).digest("hex").slice(0, 20);
}

export function getDashboardSnapshot() {
  const cached = globalThis[CACHE_KEY];
  if (cached && readPipelineStatus().state === "running") return cached;
  try {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const versionBefore = dashboardVersion();
      if (cached?.version === versionBefore) return cached;
      const data = loadDashboardData();
      const versionAfter = dashboardVersion();
      if (versionBefore === versionAfter) {
        const snapshot = {
          version: versionAfter,
          loadedAt: new Date().toISOString(),
          degraded: false,
          data,
        };
        globalThis[CACHE_KEY] = snapshot;
        return snapshot;
      }
    }
    throw new Error("Dashboard source files changed while loading");
  } catch (error) {
    if (cached) {
      console.error(`Using last-known-good dashboard snapshot: ${error?.name ?? "Error"}`);
      return { ...cached, degraded: true };
    }
    throw error;
  }
}

export function readPipelineStatus() {
  try {
    return JSON.parse(fs.readFileSync(PIPELINE_STATUS_FILE, "utf-8"));
  } catch (error) {
    if (error?.code === "ENOENT") return { state: "idle", lastRun: null };
    return { state: "unknown", lastRun: null };
  }
}
