import { existsSync } from "node:fs";
import { readFile, rename, unlink, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as adk from "admdongkor";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const availableVersions = adk.versions();
const remoteVersions = (await adk.versionsAsync(undefined, {
  signal: AbortSignal.timeout(30_000),
})).toSorted();
const latestRemoteVersion = remoteVersions.at(-1);
const version = process.env.BOUNDARY_VERSION || latestRemoteVersion;

if (!availableVersions.includes(version)) {
  throw new Error(
    `설치된 admdongkor가 최신 원격 경계 ${version}를 지원하지 않습니다. 의존성을 갱신하세요.`,
  );
}

function roundCoordinates(value) {
  if (Array.isArray(value)) return value.map(roundCoordinates);
  return Number(value.toFixed(5));
}

const source = await adk.get(version, "sgg", { detail: false, signal: AbortSignal.timeout(60_000) });
const features = source.features.map((feature) => ({
  type: "Feature",
  geometry: { ...feature.geometry, coordinates: roundCoordinates(feature.geometry.coordinates) },
  properties: {
    code: String(feature.properties.sggcd),
    name: feature.properties.sggnm,
    sidoCode: String(feature.properties.sidocd),
    sido: feature.properties.sidonm,
    areaSqm: Math.round(feature.properties.area),
  },
}));

const featureCodes = features.map((feature) => feature.properties.code);
if (new Set(featureCodes).size !== featureCodes.length) {
  throw new Error("시군구 경계 코드가 중복됩니다.");
}
if (source.crs !== "EPSG:4326") {
  throw new Error(`웹 지도 경계 좌표계가 EPSG:4326이 아닙니다: ${source.crs}`);
}

const byCode = new Map(features.map((feature) => [feature.properties.code, feature.properties.name]));
const requiredIncheon = new Map([
  ["28125", "제물포구"],
  ["28155", "영종구"],
  ["28275", "서해구"],
  ["28290", "검단구"],
]);
for (const [code, name] of requiredIncheon) {
  if (byCode.get(code) !== name) throw new Error(`2026 인천 경계 누락: ${code} ${name}`);
}
for (const legacyCode of ["28110", "28140", "28260"]) {
  if (byCode.has(legacyCode)) throw new Error(`폐지된 인천 경계 코드가 남아 있습니다: ${legacyCode}`);
}

const output = {
  type: "FeatureCollection",
  metadata: {
    version,
    upstreamDataVersion: await adk.dataVersion({ signal: AbortSignal.timeout(30_000) }),
    packageVersion: JSON.parse(
      await readFile(path.join(root, "node_modules", "admdongkor", "package.json"), "utf8"),
    ).version,
    level: "sgg",
    crs: source.crs,
    resolution: "light",
    limitation: "시각화용 단순화 경계이며 법적·측량용 공식 원본이 아님",
    source: "https://github.com/vuski/admdongkor",
    upstreamSource: "Statistics Korea SGIS",
    license: "CC-BY-4.0; KOGL-Type-1 attribution required",
    attribution: "통계청 통계지리정보서비스(SGIS) 공공누리 제1유형 경계 가공: vuski/admdongkor",
  },
  features,
};
const requestedOutput = process.env.BOUNDARY_OUTPUT;
const liveOutput = path.join(root, "src", "data", "koreaGeo.json");
const outputPath = requestedOutput
  ? path.resolve(requestedOutput)
  : path.join(root, "src", "data", `.koreaGeo.${process.pid}.staged.json`);
const temporaryPath = `${outputPath}.tmp`;
await writeFile(temporaryPath, JSON.stringify(output), "utf8");
await rename(temporaryPath, outputPath);

if (requestedOutput) {
  console.log(`Saved ${features.length} boundaries (${version}) to ${outputPath}`);
} else {
  const windowsVenv = path.join(root, ".venv", "Scripts", "python.exe");
  const unixVenv = path.join(root, ".venv", "bin", "python");
  const configuredPython = process.env.PIPELINE_PYTHON;
  const python = configuredPython
    || (existsSync(windowsVenv) ? windowsVenv : null)
    || (existsSync(unixVenv) ? unixVenv : null)
    || (process.platform === "win32" ? "py" : "python3");
  const pythonArgs = process.platform === "win32" && python === "py" ? ["-3.12"] : [];
  const validation = spawnSync(
    python,
    [...pythonArgs, path.join(root, "scripts", "validate_data_contract.py")],
    {
      cwd: root,
      env: {
        ...process.env,
        BOUNDARY_FILE: outputPath,
        PYTHONIOENCODING: process.env.PYTHONIOENCODING || "utf-8",
      },
      stdio: "inherit",
    },
  );
  if (validation.error || validation.status !== 0) {
    await unlink(outputPath).catch(() => {});
    throw validation.error || new Error(`경계 데이터 계약 검증 실패: exit=${validation.status}`);
  }
  await rename(outputPath, liveOutput);
  console.log(`Validated and promoted ${features.length} boundaries (${version}) to ${liveOutput}`);
}
