import assert from "node:assert/strict";
import test from "node:test";

import { FULL_EXAMPLE, REFERENCE_EXAMPLE } from "../examples.mjs";
import {
  canonicalHashFromJsonText as browserCanonicalHash,
  verifyCarrierText,
} from "../verifier.mjs";
import { canonicalHashFromJsonText as nodeCanonicalHash } from "../../protocol/verify_hash_tree_v1.mjs";

test("the full release example receives exact identity proof", async () => {
  const result = await verifyCarrierText(FULL_EXAMPLE);
  assert.equal(result.ok, true);
  assert.equal(result.kind, "full");
  assert.equal(result.proofLevel, "exact");
  assert.equal(result.releaseVersion, "0.13.6");
  assert.equal(result.disclosureAtDeclaredAsOf, "full");
  assert.equal(result.recordHash, result.computedHash);
  assert.equal(
    result.recordHash,
    "a459bd4c9d12565239d6c65ac88a521bde6d86d49af48b78dc15504f4c4b393b",
  );
  assert.equal(result.checks.length, 6);
});

test("the redacted release example receives linked proof without a fake rehash", async () => {
  const result = await verifyCarrierText(REFERENCE_EXAMPLE);
  assert.equal(result.ok, true);
  assert.equal(result.kind, "reference");
  assert.equal(result.proofLevel, "linked");
  assert.equal(result.computedHash, null);
  assert.deepEqual(result.reasonCodes, ["redistribution_not_permitted"]);
  assert.equal(result.disclosureAtDeclaredAsOf, "metadata_only");
});

test("payload tampering fails the recomputed record hash", async () => {
  const tampered = FULL_EXAMPLE.replace(
    '"purpose": "conformance demonstration"',
    '"purpose": "tampered"',
  );
  const result = await verifyCarrierText(tampered);
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "identity");
  assert.equal(result.error.path, "record_hash");
});

test("the authority boundary fails before identity is considered", async () => {
  const elevated = FULL_EXAMPLE.replace('"can_execute": false', '"can_execute": true');
  const result = await verifyCarrierText(elevated);
  assert.equal(result.ok, false);
  assert.equal(result.error.path, "authority");
  assert.match(result.error.message, /all-false/u);
});

test("duplicate JSON keys fail closed", async () => {
  const duplicate = FULL_EXAMPLE.replace(
    '"authority": {',
    '"authority": {},\n  "authority": {',
  );
  const result = await verifyCarrierText(duplicate);
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "json");
  assert.match(result.error.message, /duplicate object key/u);
});

test("unknown root fields fail the exact v1 contract", async () => {
  const value = JSON.parse(FULL_EXAMPLE);
  value.telemetry = true;
  const result = await verifyCarrierText(JSON.stringify(value));
  assert.equal(result.ok, false);
  assert.equal(result.error.path, "carrier");
  assert.match(result.error.message, /unsupported fields: telemetry/u);
});

test("a reference cannot smuggle a payload", async () => {
  const value = JSON.parse(REFERENCE_EXAMPLE);
  value.payload = { hidden: false };
  const result = await verifyCarrierText(JSON.stringify(value));
  assert.equal(result.ok, false);
  assert.equal(result.error.path, "reference");
  assert.match(result.error.message, /unsupported fields: payload/u);
});

test("browser hash trees match the release Node verifier for hard numbers", async () => {
  const source = '{"z":9007199254740993,"a":-0.0,"emoji":"😀","nested":[1e-7,null]}';
  const browser = await browserCanonicalHash(source);
  const node = nodeCanonicalHash(source);
  assert.equal(browser.canonical, node.canonical);
  assert.equal(browser.digest, node.digest);
  assert.deepEqual(browser.tree, node.tree);
});

test("invalid calendar dates fail temporal validation", async () => {
  const invalid = FULL_EXAMPLE.replace(
    '"event_time": "2026-08-24T11:26:45Z"',
    '"event_time": "2026-02-30T11:26:45Z"',
  );
  const result = await verifyCarrierText(invalid);
  assert.equal(result.ok, false);
  assert.equal(result.error.path, "clocks.event_time");
});

test("HTTPS URLs with user information fail closed", async () => {
  const invalid = FULL_EXAMPLE.replace(
    '"endpoint": "https://liquilens.in/protocol/"',
    '"endpoint": "https://user@example.com/protocol/"',
  );
  const result = await verifyCarrierText(invalid);
  assert.equal(result.ok, false);
  assert.equal(result.error.path, "producer.endpoint");
});

test("empty and oversized inputs fail before parsing", async () => {
  const empty = await verifyCarrierText("  ");
  assert.equal(empty.ok, false);
  assert.equal(empty.error.code, "input");

  const oversized = await verifyCarrierText(`{"padding":"${"x".repeat(1_048_576)}"}`);
  assert.equal(oversized.ok, false);
  assert.equal(oversized.error.code, "input");
});
