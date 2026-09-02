import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  InMemoryReceiptConsumer,
  PaperTradeSafetyOrderGateway,
  TradeSafetyOrderBlocked,
  verifyTradeSafetyReceipt,
} from "../src/index.mjs";

const corpusUrl = new URL(
  "../../../protocol/conformance/trade-safety-v1/corpus.json",
  import.meta.url,
);
const corpus = JSON.parse(await readFile(corpusUrl, "utf8"));
const hmacKey = Buffer.from(corpus.fixture_hmac_key_base64, "base64");

for (const vector of corpus.cases) {
  test(`conformance: ${vector.id}`, async () => {
    const request = Buffer.from(vector.request_utf8_base64, "base64");
    const receipt = Buffer.from(vector.receipt_utf8_base64, "base64");
    const clock = () => new Date(vector.evaluated_at);

    if (vector.expected.verifier_ok) {
      const verified = verifyTradeSafetyReceipt(receipt, {
        evaluatedAt: vector.evaluated_at,
        hmacKey,
      });
      assert.equal(verified.authenticated, true);
    } else {
      assert.throws(
        () =>
          verifyTradeSafetyReceipt(receipt, {
            evaluatedAt: vector.evaluated_at,
            hmacKey,
          }),
      );
    }

    let submissions = 0;
    const gateway = new PaperTradeSafetyOrderGateway(
      async (authorization) => {
        submissions += 1;
        assert.equal(authorization.authenticated, true);
        return authorization.receiptId;
      },
      {
        binding: vector.binding,
        receiptConsumer: new InMemoryReceiptConsumer({ clock }),
        hmacKey,
        clock,
      },
    );

    if (vector.expected.guard === "submit") {
      const result = await gateway.submit(request, receipt);
      assert.match(result, /^trade_safety_[0-9a-f]{24}$/u);
      assert.equal(submissions, 1);
      return;
    }

    if (vector.expected.attempts === 2) {
      await gateway.submit(request, receipt);
      assert.equal(submissions, 1);
    }
    await assert.rejects(
      () => gateway.submit(request, receipt),
      (error) => {
        assert.ok(error instanceof TradeSafetyOrderBlocked);
        assert.equal(error.reasonCode, vector.expected.guard);
        return true;
      },
    );
    assert.equal(submissions, vector.expected.attempts === 2 ? 1 : 0);
  });
}

test("authoritative APIs reject already-parsed objects", () => {
  assert.throws(
    () => verifyTradeSafetyReceipt({}, { evaluatedAt: new Date() }),
    /raw UTF-8 bytes/u,
  );
});

test("concurrent replay attempts invoke the paper callback exactly once", async () => {
  const vector = corpus.cases.find((item) => item.id === "paper-pass");
  assert.ok(vector);
  const request = Buffer.from(vector.request_utf8_base64, "base64");
  const receipt = Buffer.from(vector.receipt_utf8_base64, "base64");
  const clock = () => new Date(vector.evaluated_at);
  let submissions = 0;
  const gateway = new PaperTradeSafetyOrderGateway(
    async () => {
      submissions += 1;
      await Promise.resolve();
      return "paper-only";
    },
    {
      binding: vector.binding,
      receiptConsumer: new InMemoryReceiptConsumer({ clock }),
      hmacKey,
      clock,
    },
  );
  const results = await Promise.allSettled(
    Array.from({ length: 16 }, () => gateway.submit(request, receipt)),
  );
  assert.equal(results.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(results.filter((item) => item.status === "rejected").length, 15);
  for (const result of results) {
    if (result.status === "rejected") {
      assert.ok(result.reason instanceof TradeSafetyOrderBlocked);
      assert.equal(result.reason.reasonCode, "receipt_replay");
    }
  }
  assert.equal(submissions, 1);
});
