import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { clearOwnedPipelineLock } from "../scripts/pipeline_lock.mjs";

function withTemporaryLock(owner, callback) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "pipeline-lock-test-"));
  const filename = path.join(directory, ".pipeline.lock");
  fs.writeFileSync(filename, String(owner), "utf8");
  try {
    callback(filename);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test("a timed-out child can clear only its own pipeline lock", () => {
  withTemporaryLock(1234, (filename) => {
    assert.deepEqual(clearOwnedPipelineLock(filename, 1234), {
      cleared: true,
      reason: "owned",
    });
    assert.equal(fs.existsSync(filename), false);
  });
});

test("a different process lock is preserved", () => {
  withTemporaryLock(5678, (filename) => {
    assert.deepEqual(clearOwnedPipelineLock(filename, 1234), {
      cleared: false,
      reason: "owner-mismatch",
    });
    assert.equal(fs.readFileSync(filename, "utf8"), "5678");
  });
});

test("an already-clean lock is a safe no-op", () => {
  const filename = path.join(os.tmpdir(), `missing-pipeline-lock-${process.pid}`);
  fs.rmSync(filename, { force: true });
  assert.deepEqual(clearOwnedPipelineLock(filename, process.pid), {
    cleared: false,
    reason: "missing",
  });
});
