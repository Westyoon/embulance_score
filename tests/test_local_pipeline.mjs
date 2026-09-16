import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import {
  applyLocalPipelineDefaults,
  LOCAL_PIPELINE_DEFAULTS,
  validateLocalPipelineEnvironment,
} from "../scripts/start_local_pipeline.mjs";

const validKeys = {
  DATA_GO_KR_API_KEY: "nemc-secret",
  HIRA_API_KEY: "hira-secret",
  KAKAO_REST_API_KEY: "kakao-secret",
};

test("local pipeline enables the scheduler and applies Korea time-window defaults", () => {
  const environment = applyLocalPipelineDefaults({
    ...validKeys,
    ENABLE_PIPELINE_SCHEDULER: "false",
  });

  for (const [name, value] of Object.entries(LOCAL_PIPELINE_DEFAULTS)) {
    assert.equal(environment[name], value);
  }
});

test("local pipeline preserves explicit schedule overrides", () => {
  const environment = applyLocalPipelineDefaults({
    ...validKeys,
    CORE_REFRESH_INTERVAL_MINUTES: "30",
    OFF_HOURS_REFRESH_INTERVAL_MINUTES: "360",
  });

  assert.equal(environment.CORE_REFRESH_INTERVAL_MINUTES, "30");
  assert.equal(environment.OFF_HOURS_REFRESH_INTERVAL_MINUTES, "360");
  assert.equal(environment.ENABLE_PIPELINE_SCHEDULER, "true");
});

test("local pipeline rejects missing and example-placeholder API keys without exposing values", () => {
  const environment = applyLocalPipelineDefaults({
    DATA_GO_KR_API_KEY: "actual-value-must-not-appear",
    HIRA_API_KEY: "<HIRA key>",
    KAKAO_REST_API_KEY: "",
  });

  assert.throws(
    () => validateLocalPipelineEnvironment(environment, { requireBuild: false }),
    (error) => {
      assert.match(error.message, /HIRA_API_KEY/);
      assert.match(error.message, /KAKAO_REST_API_KEY/);
      assert.doesNotMatch(error.message, /actual-value-must-not-appear/);
      return true;
    },
  );
});

test("local pipeline refuses to use the repository root as mutable runtime storage", () => {
  const environment = applyLocalPipelineDefaults({
    ...validKeys,
    PIPELINE_RUNTIME_DIR: ".",
  });

  assert.throws(
    () => validateLocalPipelineEnvironment(environment, { requireBuild: false }),
    /PIPELINE_RUNTIME_DIR/,
  );
});

test("local pipeline refuses a parent-directory runtime path", () => {
  const environment = applyLocalPipelineDefaults({
    ...validKeys,
    PIPELINE_RUNTIME_DIR: "..",
  });

  assert.throws(
    () => validateLocalPipelineEnvironment(environment, { requireBuild: false }),
    /PIPELINE_RUNTIME_DIR/,
  );
});

test("local pipeline refuses an absolute runtime path outside the repository", () => {
  const root = process.cwd();
  const outsideRuntime = path.resolve(root, "..", "outside-local-pipeline-runtime");
  assert.equal(path.isAbsolute(outsideRuntime), true);
  const environment = applyLocalPipelineDefaults({
    ...validKeys,
    PIPELINE_RUNTIME_DIR: outsideRuntime,
  });

  assert.throws(
    () => validateLocalPipelineEnvironment(environment, { root, requireBuild: false }),
    /PIPELINE_RUNTIME_DIR/,
  );
});

test("local pipeline validates schedule settings before starting the runtime", () => {
  const environment = applyLocalPipelineDefaults({
    ...validKeys,
    REFRESH_TIME_ZONE: "Not/A_Time_Zone",
  });

  assert.throws(
    () => validateLocalPipelineEnvironment(environment, { requireBuild: false }),
    /refreshTimeZone/,
  );
});
