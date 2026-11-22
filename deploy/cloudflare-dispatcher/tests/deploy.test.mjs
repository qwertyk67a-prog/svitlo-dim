import test from "node:test";
import assert from "node:assert/strict";
import { installation } from "../deploy.mjs";

test("each checkout accepts only its own installation", () => {
  for (const repository of ["example/first", "example/second"]) {
    const raw = JSON.stringify({ worker: "demo", namespace: "a".repeat(32), repository });
    assert.equal(installation(raw, repository).repository, repository);
    assert.throws(() => installation(raw, "example/other"), /another repository/);
  }
  assert.throws(() => installation("", "example/first"), /build secret/);
  assert.throws(() => installation("{}", "example/first"), /Invalid/);
});
