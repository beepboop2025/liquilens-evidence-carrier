import assert from "node:assert/strict";
import test from "node:test";

import { FULL_EXAMPLE, REFERENCE_EXAMPLE } from "../../../browser/examples.mjs";
import { verifyCarrierText } from "../../../browser/verifier.mjs";
import {
  failureDetails,
  isCarrierFilename,
  successMessage,
} from "../src/diagnostics.mjs";
import { createLatestOnlyGuard } from "../src/latest.mjs";

test("carrier filename matching is narrow and cross-platform", () => {
  assert.equal(isCarrierFilename("/repo/order.evidence.json"), true);
  assert.equal(isCarrierFilename("C:\\repo\\order.carrier.json"), true);
  assert.equal(isCarrierFilename("/repo/evidence.json"), false);
  assert.equal(isCarrierFilename("/repo/order.json"), false);
  assert.equal(isCarrierFilename("/repo/order.evidence.json.txt"), false);
});

test("full and reference evidence produce bounded success messages", async () => {
  const full = await verifyCarrierText(FULL_EXAMPLE);
  const reference = await verifyCarrierText(REFERENCE_EXAMPLE);

  assert.equal(failureDetails(full), null);
  assert.match(successMessage(full), /verified exact evidence/u);
  assert.match(successMessage(full), /disposition full/u);
  assert.equal(failureDetails(reference), null);
  assert.match(successMessage(reference), /verified linked evidence/u);
  assert.match(successMessage(reference), /disposition metadata_only/u);
});

test("tampering becomes a stable fail-closed diagnostic", async () => {
  const tampered = FULL_EXAMPLE.replace(
    '"purpose": "conformance demonstration"',
    '"purpose": "tampered"',
  );
  const result = await verifyCarrierText(tampered);

  assert.deepEqual(failureDetails(result), {
    code: "liquilens-identity",
    message: "record_hash: does not match the canonical carrier payload",
  });
});

test("unknown verifier failures do not disappear", () => {
  assert.deepEqual(failureDetails(undefined), {
    code: "liquilens-runtime",
    message: "carrier: verification could not complete",
  });
});

test("only the latest unchanged document snapshot may publish a result", () => {
  const guard = createLatestOnlyGuard();
  const old = guard.begin("file:///carrier.evidence.json", 7);
  const current = guard.begin("file:///carrier.evidence.json", 8);

  assert.equal(guard.isCurrent(old, 7), false);
  assert.equal(guard.isCurrent(current, 8), true);
  assert.equal(guard.isCurrent(current, 9), false);

  guard.invalidate("file:///carrier.evidence.json");
  assert.equal(guard.isCurrent(current, 8), false);

  const reopened = guard.begin("file:///carrier.evidence.json", 1);
  assert.equal(guard.isCurrent(old, 7), false);
  assert.equal(guard.isCurrent(reopened, 1), true);
});
