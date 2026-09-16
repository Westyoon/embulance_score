import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { normalizeScheduleConfig } from "./pipeline_schedule.mjs";

export const REQUIRED_LOCAL_PIPELINE_KEYS = Object.freeze([
  "DATA_GO_KR_API_KEY",
  "HIRA_API_KEY",
  "KAKAO_REST_API_KEY",
]);

export const LOCAL_PIPELINE_DEFAULTS = Object.freeze({
  ENABLE_PIPELINE_SCHEDULER: "true",
  PIPELINE_RUNTIME_DIR: "./runtime",
  REFRESH_TIME_ZONE: "Asia/Seoul",
  CORE_REFRESH_START_HOUR: "21",
  CORE_REFRESH_END_HOUR: "9",
  CORE_REFRESH_INTERVAL_MINUTES: "480",
  OFF_HOURS_REFRESH_INTERVAL_MINUTES: "480",
});

function missingSecret(value) {
  const normalized = String(value ?? "").trim();
  return !normalized || /^<[^>]+>$/.test(normalized);
}

export function applyLocalPipelineDefaults(environment) {
  for (const [name, value] of Object.entries(LOCAL_PIPELINE_DEFAULTS)) {
    if (name === "ENABLE_PIPELINE_SCHEDULER" || !String(environment[name] ?? "").trim()) {
      environment[name] = value;
    }
  }
  return environment;
}

export function validateLocalPipelineEnvironment(
  environment,
  { root = process.cwd(), requireBuild = true } = {},
) {
  const missingKeys = REQUIRED_LOCAL_PIPELINE_KEYS.filter(
    (name) => missingSecret(environment[name]),
  );
  if (missingKeys.length > 0) {
    throw new Error(
      "로컬 자동 수집에 필요한 서버 API 키가 없습니다: "
      + `${missingKeys.join(", ")}. .env에 값을 설정하세요.`,
    );
  }

  const configuredRuntime = environment.PIPELINE_RUNTIME_DIR?.trim() || "./runtime";
  const repositoryRoot = path.resolve(root);
  const runtimeRoot = path.resolve(repositoryRoot, configuredRuntime);
  const relativeRuntime = path.relative(repositoryRoot, runtimeRoot);
  const runtimeIsStrictDescendant = relativeRuntime !== ""
    && relativeRuntime !== ".."
    && !relativeRuntime.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relativeRuntime);
  if (!runtimeIsStrictDescendant) {
    throw new Error(
      "PIPELINE_RUNTIME_DIR은 저장소 루트 내부의 별도 경로여야 합니다. "
      + "./runtime 같은 하위 경로를 사용하세요.",
    );
  }

  if (requireBuild && !fs.existsSync(path.join(root, ".next", "BUILD_ID"))) {
    throw new Error(
      "Next.js 운영 빌드가 없습니다. 먼저 npm run build를 실행하세요.",
    );
  }

  const schedule = normalizeScheduleConfig({
    refreshTimeZone: environment.REFRESH_TIME_ZONE,
    coreRefreshStartHour: environment.CORE_REFRESH_START_HOUR,
    coreRefreshEndHour: environment.CORE_REFRESH_END_HOUR,
    coreRefreshIntervalMinutes: environment.CORE_REFRESH_INTERVAL_MINUTES,
    offHoursRefreshIntervalMinutes: environment.OFF_HOURS_REFRESH_INTERVAL_MINUTES,
  });

  return { runtimeRoot, schedule, missingKeys: [] };
}

function loadLocalEnvironment(root) {
  const envFile = path.join(root, ".env");
  if (fs.existsSync(envFile)) process.loadEnvFile(envFile);
}

async function main() {
  const root = process.cwd();
  loadLocalEnvironment(root);
  applyLocalPipelineDefaults(process.env);
  const { runtimeRoot, schedule } = validateLocalPipelineEnvironment(process.env, { root });

  if (process.argv.includes("--check")) {
    console.log(
      `[local-pipeline] configuration is valid; runtime=${runtimeRoot}; `
      + `schedule=${schedule.refreshTimeZone} `
      + `${String(schedule.coreRefreshStartHour).padStart(2, "0")}:00-`
      + `${String(schedule.coreRefreshEndHour).padStart(2, "0")}:00 `
      + `every ${schedule.coreRefreshIntervalMinutes}m, off-hours `
      + `every ${schedule.offHoursRefreshIntervalMinutes}m`,
    );
    return;
  }

  console.log(`[local-pipeline] validated local configuration; runtime=${runtimeRoot}`);
  await import("./start_dynamic.mjs");
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(`[local-pipeline] ${error.message}`);
    process.exitCode = 1;
  });
}
