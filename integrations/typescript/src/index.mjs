import { createHmac, timingSafeEqual } from "node:crypto";

import {
  canonicalValuesEqual,
  digestValue,
  isJsonObject,
  numberValue,
  omitObjectFields,
  parseJsonUtf8,
} from "./hash_tree_v1.mjs";

export const TRADE_SAFETY_HMAC_DOMAIN = "liquilens.trade-safety-receipt.v1\n";
const RECEIPT_FIELDS = new Set([
  "schema",
  "canonicalization",
  "receipt_id",
  "record_hash",
  "evaluated_at",
  "expires_at",
  "request",
  "request_hash",
  "policy",
  "policy_hash",
  "evidence",
  "broker_preview",
  "decision",
  "issuer",
  "integrity",
  "authority",
]);
const AUTHORITY = {
  financial_authority: "operator_policy_check_only",
  can_execute: false,
  can_recommend: false,
  can_allocate_capital: false,
  is_credit_rating: false,
  is_executable_quote: false,
};
const PRODUCTS = ["seiche", "undertow", "liquilens"];
const ASSET_CLASSES = new Set([
  "crypto", "equity", "etf", "fixed_income", "fx", "commodity", "derivative", "other",
]);
const REGIMES = new Set(["CALM", "EROSION", "STRAIN", "STRESS"]);
const EVIDENCE_CLASSES = new Set([
  "observed", "derived", "structural", "research", "restricted", "unavailable",
]);
const RIGHTS_STATUSES = new Set([
  "licensed", "allowed", "metadata_only", "restricted", "unknown", "blocked",
]);
const UNSAFE_RIGHTS = new Set(["restricted", "unknown", "blocked"]);
const REAL_MONEY_RIGHTS = new Set(["allowed", "licensed"]);

export class TradeSafetyVerificationError extends Error {}

export class TradeSafetyOrderBlocked extends TradeSafetyVerificationError {
  constructor(reasonCode, message, details = {}) {
    super(`${reasonCode}: ${message}`);
    this.name = "TradeSafetyOrderBlocked";
    this.reasonCode = reasonCode;
    this.outcome = details.outcome ?? null;
    this.receiptId = details.receiptId ?? null;
  }
}

function exactKeys(value, fieldName, expected) {
  if (!isJsonObject(value)) throw new Error(`${fieldName} must be an object`);
  const actual = new Set(Object.keys(value));
  const missing = [...expected].filter((key) => !actual.has(key)).sort();
  const extra = [...actual].filter((key) => !expected.has(key)).sort();
  if (missing.length > 0) throw new Error(`${fieldName} is missing fields: ${missing.join(", ")}`);
  if (extra.length > 0) throw new Error(`${fieldName} has unsupported fields: ${extra.join(", ")}`);
  return value;
}

function text(value, fieldName, { nullable = false } = {}) {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${fieldName} must be a non-blank string`);
  }
  return value;
}

function boolean(value, fieldName) {
  if (typeof value !== "boolean") throw new Error(`${fieldName} must be boolean`);
  return value;
}

function sha256(value, fieldName, { nullable = false } = {}) {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/u.test(value)) {
    throw new Error(`${fieldName} must be lowercase SHA-256`);
  }
  return value;
}

function timestamp(value, fieldName) {
  if (typeof value !== "string" || !value.endsWith("Z")) {
    throw new Error(`${fieldName} must be a UTC timestamp ending in Z`);
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?Z$/u.exec(value);
  if (match === null) throw new Error(`${fieldName} is not a valid timestamp`);
  const [, yearText, monthText, dayText, hourText, minuteText, secondText] = match;
  const parts = [yearText, monthText, dayText, hourText, minuteText, secondText].map(Number);
  const [year, month, day, hour, minute, second] = parts;
  if (year === 0 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) {
    throw new Error(`${fieldName} is not a valid timestamp`);
  }
  const dayCheck = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  if (
    dayCheck.getUTCFullYear() !== year ||
    dayCheck.getUTCMonth() !== month - 1 ||
    dayCheck.getUTCDate() !== day
  ) {
    throw new Error(`${fieldName} is not a valid timestamp`);
  }
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) throw new Error(`${fieldName} is not a valid timestamp`);
  return milliseconds;
}

function httpsUrl(value, fieldName, { nullable = false } = {}) {
  if (nullable && value === null) return null;
  const item = text(value, fieldName);
  let parsed;
  try {
    parsed = new URL(item);
  } catch (error) {
    throw new Error(`${fieldName} must be an HTTPS URL without userinfo`, { cause: error });
  }
  if (parsed.protocol !== "https:" || parsed.host === "" || parsed.username !== "" || parsed.password !== "") {
    throw new Error(`${fieldName} must be an HTTPS URL without userinfo`);
  }
  return item;
}

function stringArray(value, fieldName, { allowEmpty = false } = {}) {
  if (!Array.isArray(value) || (!allowEmpty && value.length === 0)) {
    throw new Error(`${fieldName} must be ${allowEmpty ? "an" : "a non-empty"} array`);
  }
  for (const item of value) text(item, `${fieldName} item`);
  if (new Set(value).size !== value.length) throw new Error(`${fieldName} must not contain duplicates`);
  return value;
}

function positiveNumber(value, fieldName, { nullable = false } = {}) {
  if (nullable && value === null) return null;
  const result = numberValue(value, fieldName);
  if (result <= 0) throw new Error(`${fieldName} must be a finite positive number`);
  return result;
}

function nonnegativeNumber(value, fieldName) {
  const result = numberValue(value, fieldName);
  if (result < 0) throw new Error(`${fieldName} must be a finite non-negative number`);
  return result;
}

function positiveInteger(value, fieldName) {
  const result = positiveNumber(value, fieldName);
  if (!Number.isSafeInteger(result) || value.kind !== "integer") {
    throw new Error(`${fieldName} must be a positive safe integer`);
  }
  return result;
}

function validateRequest(request) {
  exactKeys(request, "request", new Set([
    "schema", "request_id", "created_at", "expires_at", "mode", "agent", "order", "policy_ref", "extensions",
  ]));
  if (request.schema !== "liquilens.trade-safety-request.v1") throw new Error("request.schema is unsupported");
  if (typeof request.request_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/u.test(request.request_id)) {
    throw new Error("request.request_id has an invalid shape");
  }
  const createdAt = timestamp(request.created_at, "request.created_at");
  const expiresAt = timestamp(request.expires_at, "request.expires_at");
  if (expiresAt <= createdAt) throw new Error("request.expires_at must follow request.created_at");
  if (!["observe", "paper", "live"].includes(request.mode)) throw new Error("request.mode is unsupported");

  const agent = exactKeys(request.agent, "request.agent", new Set([
    "agent_id", "operator_id", "tenant_id", "account_id", "runtime", "strategy_id", "authorization_scope",
  ]));
  for (const key of ["agent_id", "operator_id", "tenant_id", "account_id", "runtime"]) {
    text(agent[key], `request.agent.${key}`);
  }
  text(agent.strategy_id, "request.agent.strategy_id", { nullable: true });
  const scope = stringArray(agent.authorization_scope, "request.agent.authorization_scope");
  if (request.mode === "paper" && !scope.includes("orders:paper")) {
    throw new Error("paper mode requires orders:paper authorization");
  }
  if (request.mode === "live" && !scope.includes("orders:live")) {
    throw new Error("live mode requires orders:live authorization");
  }

  const order = exactKeys(request.order, "request.order", new Set([
    "instrument", "side", "order_type", "notional", "quantity", "limit_price", "stop_price", "venue", "time_in_force",
  ]));
  const instrument = exactKeys(order.instrument, "request.order.instrument", new Set(["asset_class", "symbol", "identifiers"]));
  if (!ASSET_CLASSES.has(instrument.asset_class)) {
    throw new Error("request.order.instrument.asset_class is unsupported");
  }
  text(instrument.symbol, "request.order.instrument.symbol");
  if (!isJsonObject(instrument.identifiers)) throw new Error("request.order.instrument.identifiers must be an object");
  for (const [key, value] of Object.entries(instrument.identifiers)) {
    text(key, "request.order.instrument.identifiers key");
    if (/\s/u.test(key)) throw new Error("request.order.instrument.identifiers keys must be non-blank tokens");
    text(value, `request.order.instrument.identifiers.${key}`);
  }
  if (!["buy", "sell"].includes(order.side)) throw new Error("request.order.side is unsupported");
  const orderType = order.order_type;
  if (!["market", "limit", "stop", "stop_limit", "other"].includes(orderType)) {
    throw new Error("request.order.order_type is unsupported");
  }
  const notional = exactKeys(order.notional, "request.order.notional", new Set(["amount", "currency"]));
  positiveNumber(notional.amount, "request.order.notional.amount");
  if (typeof notional.currency !== "string" || !/^[A-Z]{3}$/u.test(notional.currency)) {
    throw new Error("request.order.notional.currency must be an uppercase three-letter code");
  }
  positiveNumber(order.quantity, "request.order.quantity", { nullable: true });
  const limitPrice = positiveNumber(order.limit_price, "request.order.limit_price", { nullable: true });
  const stopPrice = positiveNumber(order.stop_price, "request.order.stop_price", { nullable: true });
  if (["limit", "stop_limit"].includes(orderType) && limitPrice === null) {
    throw new Error("request.order.limit_price is required for limit and stop_limit orders");
  }
  if (["stop", "stop_limit"].includes(orderType) && stopPrice === null) {
    throw new Error("request.order.stop_price is required for stop and stop_limit orders");
  }
  if (!["limit", "stop_limit"].includes(orderType) && limitPrice !== null) {
    throw new Error("request.order.limit_price is only valid for limit and stop_limit orders");
  }
  if (!["stop", "stop_limit"].includes(orderType) && stopPrice !== null) {
    throw new Error("request.order.stop_price is only valid for stop and stop_limit orders");
  }
  text(order.venue, "request.order.venue", { nullable: true });
  text(order.time_in_force, "request.order.time_in_force");
  const policyRef = exactKeys(request.policy_ref, "request.policy_ref", new Set(["policy_id", "version"]));
  text(policyRef.policy_id, "request.policy_ref.policy_id");
  text(policyRef.version, "request.policy_ref.version");
  if (!isJsonObject(request.extensions) || Object.keys(request.extensions).length !== 0) {
    throw new Error("request.extensions must be an empty object in v1");
  }
  return request;
}

function validatePolicy(policy) {
  exactKeys(policy, "policy", new Set([
    "schema", "policy_id", "version", "required_products", "max_evidence_age_seconds", "hold_regimes",
    "max_notional_usd", "max_exit_cost_bps", "max_venue_spread_bps", "missing_evidence",
    "live_requires_executable_quote", "live_requires_broker_preview", "auto_resize", "extensions",
  ]));
  if (policy.schema !== "liquilens.trade-safety-policy.v1") throw new Error("policy.schema is unsupported");
  text(policy.policy_id, "policy.policy_id");
  text(policy.version, "policy.version");
  const products = stringArray(policy.required_products, "policy.required_products");
  if (!products.every((product) => PRODUCTS.includes(product))) {
    throw new Error("policy.required_products contains an unsupported product");
  }
  if (!products.includes("seiche") || !products.includes("undertow")) {
    throw new Error("policy.required_products must include both seiche and undertow");
  }
  const maxAge = exactKeys(policy.max_evidence_age_seconds, "policy.max_evidence_age_seconds", new Set(PRODUCTS));
  for (const product of PRODUCTS) positiveInteger(maxAge[product], `policy.max_evidence_age_seconds.${product}`);
  const holdRegimes = stringArray(policy.hold_regimes, "policy.hold_regimes", { allowEmpty: true });
  if (!holdRegimes.every((regime) => REGIMES.has(regime))) {
    throw new Error("policy.hold_regimes contains an unsupported value");
  }
  positiveNumber(policy.max_notional_usd, "policy.max_notional_usd", { nullable: true });
  positiveNumber(policy.max_exit_cost_bps, "policy.max_exit_cost_bps", { nullable: true });
  positiveNumber(policy.max_venue_spread_bps, "policy.max_venue_spread_bps", { nullable: true });
  if (policy.missing_evidence !== "fail_closed") throw new Error("policy.missing_evidence must be fail_closed");
  if (policy.live_requires_executable_quote !== true) {
    throw new Error("policy.live_requires_executable_quote cannot be disabled");
  }
  if (policy.live_requires_broker_preview !== true) {
    throw new Error("policy.live_requires_broker_preview cannot be disabled");
  }
  if (policy.auto_resize !== false) throw new Error("policy.auto_resize must be false");
  if (!isJsonObject(policy.extensions) || Object.keys(policy.extensions).length !== 0) {
    throw new Error("policy.extensions must be an empty object in v1");
  }
  return policy;
}

function validateEvidence(evidence) {
  exactKeys(evidence, "evidence", new Set(PRODUCTS));
  for (const product of PRODUCTS) {
    const section = exactKeys(evidence[product], `evidence.${product}`, new Set([
      "product", "request_hash", "state", "evidence_class", "rights_status", "real_money_eligible", "executable_quote",
      "source_schema", "source_url", "source_sha256", "knowledge_time", "as_of", "retrieved_at", "expires_at", "limitations", "facts",
    ]));
    if (section.product !== product) throw new Error(`evidence.${product}.product mismatch`);
    sha256(section.request_hash, `evidence.${product}.request_hash`);
    if (!["eligible", "context_only", "unavailable", "not_applicable"].includes(section.state)) throw new Error(`evidence.${product}.state is unsupported`);
    if (!EVIDENCE_CLASSES.has(section.evidence_class)) throw new Error(`evidence.${product}.evidence_class is unsupported`);
    if (!RIGHTS_STATUSES.has(section.rights_status)) throw new Error(`evidence.${product}.rights_status is unsupported`);
    boolean(section.real_money_eligible, `evidence.${product}.real_money_eligible`);
    boolean(section.executable_quote, `evidence.${product}.executable_quote`);
    text(section.source_schema, `evidence.${product}.source_schema`, { nullable: true });
    httpsUrl(section.source_url, `evidence.${product}.source_url`);
    const sourceSha = sha256(section.source_sha256, `evidence.${product}.source_sha256`, { nullable: true });
    const retrievedAt = timestamp(section.retrieved_at, `evidence.${product}.retrieved_at`);
    const asOf = section.as_of === null ? null : timestamp(section.as_of, `evidence.${product}.as_of`);
    const knowledgeTime = section.knowledge_time === null ? null : timestamp(section.knowledge_time, `evidence.${product}.knowledge_time`);
    const expiresAt = section.expires_at === null ? null : timestamp(section.expires_at, `evidence.${product}.expires_at`);
    if (asOf !== null && knowledgeTime !== null && asOf > knowledgeTime) {
      throw new Error(`evidence.${product} clocks must satisfy as_of <= knowledge_time`);
    }
    if (knowledgeTime !== null && knowledgeTime > retrievedAt) {
      throw new Error(`evidence.${product}.knowledge_time cannot follow retrieved_at`);
    }
    if (expiresAt !== null && expiresAt <= retrievedAt) {
      throw new Error(`evidence.${product}.expires_at must follow retrieved_at`);
    }
    stringArray(section.limitations, `evidence.${product}.limitations`);
    if (!isJsonObject(section.facts)) throw new Error(`evidence.${product}.facts must be an object`);
    const usable = ["eligible", "context_only"].includes(section.state);
    if (section.state === "not_applicable") {
      if ([sourceSha, asOf, knowledgeTime, expiresAt].some((item) => item !== null)) {
        throw new Error(`evidence.${product} not_applicable state cannot carry source data`);
      }
      if (Object.keys(section.facts).length !== 0) {
        throw new Error(`evidence.${product} not_applicable state must have empty facts`);
      }
    } else if (section.state === "unavailable") {
      if (section.real_money_eligible || section.executable_quote) {
        throw new Error(`evidence.${product} unavailable state cannot be eligible or quoted`);
      }
    } else if ([sourceSha, asOf, knowledgeTime, expiresAt].some((item) => item === null)) {
      throw new Error(`evidence.${product} usable state requires source hash, clocks, and expiry`);
    }
    if (UNSAFE_RIGHTS.has(section.rights_status) && section.state === "eligible") {
      throw new Error(`evidence.${product} unsafe rights cannot be marked eligible`);
    }
    if (section.executable_quote && product !== "undertow") {
      throw new Error(`evidence.${product} cannot claim to be an executable quote`);
    }
    if (section.executable_quote && !section.real_money_eligible) {
      throw new Error("an executable quote must also be marked real_money_eligible");
    }
    if (section.real_money_eligible && section.state !== "eligible") {
      throw new Error(`evidence.${product} real-money evidence must have eligible state`);
    }
    if (section.real_money_eligible && !REAL_MONEY_RIGHTS.has(section.rights_status)) {
      throw new Error(`evidence.${product} real-money evidence requires allowed or licensed rights`);
    }
    if (product === "seiche") {
      if (usable && !Object.hasOwn(section.facts, "regime")) {
        throw new Error("evidence.seiche usable state requires facts.regime");
      }
      if (Object.hasOwn(section.facts, "regime") && !REGIMES.has(section.facts.regime)) {
        throw new Error("evidence.seiche.facts.regime is unsupported");
      }
    }
    if (product === "undertow") {
      const sizeKeys = ["requested_size_usd", "published_rung_used_usd"];
      const costKeys = ["worst_sell_cost_bps", "venue_spread_bps"];
      if (usable) {
        const missing = [...sizeKeys, ...costKeys].filter((key) => !Object.hasOwn(section.facts, key));
        if (missing.length > 0) throw new Error(`evidence.undertow usable state requires normative facts: ${missing.join(", ")}`);
      }
      for (const key of sizeKeys) {
        if (section.facts[key] !== undefined && section.facts[key] !== null) {
          positiveNumber(section.facts[key], `evidence.undertow.facts.${key}`);
        }
      }
      for (const key of costKeys) {
        if (section.facts[key] !== undefined && section.facts[key] !== null) {
          nonnegativeNumber(section.facts[key], `evidence.undertow.facts.${key}`);
        }
      }
    }
  }
  return evidence;
}

function validateBrokerPreview(preview) {
  exactKeys(preview, "broker_preview", new Set([
    "schema", "state", "provider", "account_id", "request_hash", "preview_id", "source_url", "source_sha256",
    "retrieved_at", "expires_at", "limitations", "facts",
  ]));
  if (preview.schema !== "liquilens.broker-preview-reference.v1") throw new Error("broker_preview.schema is unsupported");
  if (!["verified", "unavailable", "not_applicable"].includes(preview.state)) throw new Error("broker_preview.state is unsupported");
  const provider = text(preview.provider, "broker_preview.provider", { nullable: true });
  text(preview.account_id, "broker_preview.account_id");
  sha256(preview.request_hash, "broker_preview.request_hash");
  const previewId = text(preview.preview_id, "broker_preview.preview_id", { nullable: true });
  const sourceUrl = httpsUrl(preview.source_url, "broker_preview.source_url", { nullable: true });
  const sourceSha = sha256(preview.source_sha256, "broker_preview.source_sha256", { nullable: true });
  const retrieved = timestamp(preview.retrieved_at, "broker_preview.retrieved_at");
  const expiresAt = preview.expires_at === null ? null : timestamp(preview.expires_at, "broker_preview.expires_at");
  if (expiresAt !== null && expiresAt <= retrieved) {
    throw new Error("broker_preview.expires_at must follow broker_preview.retrieved_at");
  }
  stringArray(preview.limitations, "broker_preview.limitations");
  if (!isJsonObject(preview.facts)) throw new Error("broker_preview.facts must be an object");
  if (preview.state === "verified" && [provider, previewId, sourceUrl, sourceSha, expiresAt].some((item) => item === null)) {
    throw new Error("verified broker_preview requires provider, preview identity, source identity, and expiry");
  }
  if (preview.state === "not_applicable") {
    if ([provider, previewId, sourceUrl, sourceSha, expiresAt].some((item) => item !== null)) {
      throw new Error("not_applicable broker_preview cannot carry broker source data");
    }
    if (Object.keys(preview.facts).length !== 0) {
      throw new Error("not_applicable broker_preview must have empty facts");
    }
  }
  return preview;
}

function receiptPayload(receipt) {
  const payload = omitObjectFields(receipt, new Set(["receipt_id", "record_hash"]));
  const integrity = exactKeys(payload.integrity, "receipt.integrity", new Set(["profile", "key_id", "signature"]));
  payload.integrity = Object.assign(Object.create(null), integrity, { signature: null });
  return payload;
}

function appendUnique(values, item) {
  if (!values.includes(item)) values.push(item);
}

function factNumber(section, key) {
  const value = section.facts[key];
  if (value === undefined) return null;
  try {
    const result = numberValue(value, `facts.${key}`);
    return result >= 0 ? result : null;
  } catch {
    return null;
  }
}

function expectedDecision(receipt, authenticated) {
  const { request, policy, evidence, broker_preview: preview } = receipt;
  const required = policy.required_products;
  const unavailable = [];
  const holds = [];
  const limits = [];
  const evaluatedAt = timestamp(receipt.evaluated_at, "receipt.evaluated_at");
  for (const product of required) {
    const section = evidence[product];
    if (["unavailable", "not_applicable"].includes(section.state)) {
      appendUnique(unavailable, `${product}_evidence_${section.state}`);
      continue;
    }
    if (["restricted", "unknown", "blocked"].includes(section.rights_status)) appendUnique(unavailable, `${product}_rights_not_usable`);
    const knowledge = timestamp(section.knowledge_time, `evidence.${product}.knowledge_time`);
    const asOf = timestamp(section.as_of, `evidence.${product}.as_of`);
    const retrieved = timestamp(section.retrieved_at, `evidence.${product}.retrieved_at`);
    const expires = timestamp(section.expires_at, `evidence.${product}.expires_at`);
    if (evaluatedAt < knowledge) appendUnique(unavailable, `${product}_evidence_not_yet_known`);
    if (evaluatedAt < retrieved) appendUnique(unavailable, `${product}_evidence_not_yet_retrieved`);
    if (evaluatedAt >= expires) appendUnique(unavailable, `${product}_evidence_expired`);
    if (evaluatedAt - asOf > positiveInteger(policy.max_evidence_age_seconds[product], `policy.max_evidence_age_seconds.${product}`) * 1000) appendUnique(unavailable, `${product}_evidence_too_old`);
    if (request.mode === "live" && !section.real_money_eligible) appendUnique(unavailable, `${product}_not_real_money_eligible`);
  }
  if (request.mode === "live") {
    if (!authenticated) appendUnique(unavailable, "live_receipt_authentication_missing");
    if (policy.live_requires_executable_quote && !evidence.undertow.executable_quote) appendUnique(unavailable, "undertow_executable_quote_unavailable");
    if (policy.live_requires_broker_preview && preview.state !== "verified") appendUnique(unavailable, "broker_preview_unavailable");
    if (preview.state === "verified") {
      if (evaluatedAt < timestamp(preview.retrieved_at, "broker_preview.retrieved_at")) appendUnique(unavailable, "broker_preview_not_yet_retrieved");
      if (evaluatedAt >= timestamp(preview.expires_at, "broker_preview.expires_at")) appendUnique(unavailable, "broker_preview_expired");
    }
  }
  const regime = evidence.seiche.facts.regime;
  if (policy.hold_regimes.includes(regime)) appendUnique(holds, `seiche_regime_${String(regime).toLowerCase()}_held_by_policy`);
  const notional = positiveNumber(request.order.notional.amount, "request.order.notional.amount");
  const currency = request.order.notional.currency;
  if (currency !== "USD" && [policy.max_notional_usd, policy.max_exit_cost_bps, policy.max_venue_spread_bps].some((item) => item !== null)) appendUnique(unavailable, "usd_policy_requires_usd_order_notional");
  const maxNotional = positiveNumber(policy.max_notional_usd, "policy.max_notional_usd", { nullable: true });
  if (maxNotional !== null && currency === "USD" && notional > maxNotional) appendUnique(limits, "max_notional_usd_exceeded");
  const undertow = evidence.undertow;
  const requested = factNumber(undertow, "requested_size_usd");
  const rung = factNumber(undertow, "published_rung_used_usd");
  const exitCost = factNumber(undertow, "worst_sell_cost_bps");
  const spread = factNumber(undertow, "venue_spread_bps");
  if (currency === "USD" && requested !== null && Math.abs(requested - notional) > Math.max(0.01, notional * 1e-9)) appendUnique(unavailable, "undertow_order_size_mismatch");
  if (requested !== null && rung !== null && Math.abs(rung - requested) > Math.max(0.01, requested * 1e-9)) appendUnique(unavailable, "undertow_published_rung_mismatch");
  const maxExit = positiveNumber(policy.max_exit_cost_bps, "policy.max_exit_cost_bps", { nullable: true });
  if (maxExit !== null) {
    if (exitCost === null) appendUnique(unavailable, "undertow_exit_cost_missing");
    else if (exitCost > maxExit) appendUnique(limits, "max_exit_cost_bps_exceeded");
  }
  const maxSpread = positiveNumber(policy.max_venue_spread_bps, "policy.max_venue_spread_bps", { nullable: true });
  if (maxSpread !== null) {
    if (spread === null) appendUnique(unavailable, "undertow_venue_spread_missing");
    else if (spread > maxSpread) appendUnique(limits, "max_venue_spread_bps_exceeded");
  }
  const constraints = {
    required_products: required,
    max_notional_usd: policy.max_notional_usd,
    max_exit_cost_bps: policy.max_exit_cost_bps,
    max_venue_spread_bps: policy.max_venue_spread_bps,
    auto_resize: false,
  };
  if (unavailable.length > 0) return { outcome: "unavailable", enforced: request.mode !== "observe", reason_codes: [...unavailable, ...holds, ...limits], constraints, summary: "Required evidence or authentication is unavailable; fail closed for this exact order.", resubmit_required: false };
  if (holds.length > 0) return { outcome: "hold", enforced: request.mode !== "observe", reason_codes: [...holds, ...limits], constraints, summary: "An operator-authored policy condition holds this exact order; this is not a trade recommendation.", resubmit_required: false };
  if (limits.length > 0) return { outcome: "limit", enforced: request.mode !== "observe", reason_codes: limits, constraints, summary: "This exact order exceeds an operator-authored limit; no automatic resizing is permitted.", resubmit_required: true };
  return { outcome: "pass", enforced: request.mode !== "observe", reason_codes: ["operator_policy_satisfied"], constraints, summary: "The operator-authored policy is satisfied for this exact order; this is not approval, advice, or an execution instruction.", resubmit_required: false };
}

function safeReceiptExpiry(receipt) {
  const { request, policy, evidence, broker_preview: preview, decision } = receipt;
  const issuedAt = timestamp(receipt.evaluated_at, "receipt.evaluated_at");
  const boundaries = [
    timestamp(request.expires_at, "request.expires_at"),
    issuedAt + 3_600_000,
  ];
  if (decision.outcome !== "unavailable") {
    for (const product of policy.required_products) {
      const section = evidence[product];
      if (!["eligible", "context_only"].includes(section.state)) continue;
      boundaries.push(timestamp(section.expires_at, `evidence.${product}.expires_at`));
      boundaries.push(
        timestamp(section.as_of, `evidence.${product}.as_of`) +
          positiveInteger(
            policy.max_evidence_age_seconds[product],
            `policy.max_evidence_age_seconds.${product}`,
          ) * 1000,
      );
    }
    if (request.mode === "live" && preview.state === "verified") {
      boundaries.push(timestamp(preview.expires_at, "broker_preview.expires_at"));
    }
  }
  return Math.min(...boundaries);
}

function equalBytes(left, right) {
  return left.byteLength === right.byteLength && timingSafeEqual(left, right);
}

export function verifyTradeSafetyReceipt(receiptUtf8, { evaluatedAt, hmacKey } = {}) {
  try {
    const receipt = parseJsonUtf8(receiptUtf8, "receipt");
    exactKeys(receipt, "receipt", RECEIPT_FIELDS);
    if (receipt.schema !== "liquilens.trade-safety-receipt.v1") throw new Error("receipt.schema is unsupported");
    if (receipt.canonicalization !== "liquilens-hash-tree-v1") throw new Error("receipt.canonicalization is unsupported");
    if (typeof receipt.receipt_id !== "string" || !/^trade_safety_[0-9a-f]{24}$/u.test(receipt.receipt_id)) throw new Error("receipt.receipt_id has an invalid shape");
    sha256(receipt.record_hash, "receipt.record_hash");
    const issuedAt = timestamp(receipt.evaluated_at, "receipt.evaluated_at");
    const expiresAt = timestamp(receipt.expires_at, "receipt.expires_at");
    if (expiresAt <= issuedAt) throw new Error("receipt.expires_at must follow receipt.evaluated_at");
    const at = evaluatedAt instanceof Date ? evaluatedAt.getTime() : timestamp(evaluatedAt, "evaluatedAt");
    if (!Number.isFinite(at)) throw new Error("evaluatedAt must be a valid Date or UTC timestamp");
    if (at < issuedAt) throw new Error("receipt is not yet valid at evaluatedAt");
    if (at >= expiresAt) throw new Error("receipt is expired at evaluatedAt");

    const request = validateRequest(receipt.request);
    const policy = validatePolicy(receipt.policy);
    const evidence = validateEvidence(receipt.evidence);
    const preview = validateBrokerPreview(receipt.broker_preview);
    const issuer = exactKeys(receipt.issuer, "receipt.issuer", new Set(["name", "version", "endpoint"]));
    text(issuer.name, "receipt.issuer.name");
    text(issuer.version, "receipt.issuer.version");
    httpsUrl(issuer.endpoint, "receipt.issuer.endpoint");
    const integrity = exactKeys(receipt.integrity, "receipt.integrity", new Set(["profile", "key_id", "signature"]));
    if (!["sha256", "hmac-sha256"].includes(integrity.profile)) throw new Error("receipt.integrity.profile is unsupported");
    const authenticated = integrity.profile === "hmac-sha256";
    if (authenticated) {
      text(integrity.key_id, "receipt.integrity.key_id");
      sha256(integrity.signature, "receipt.integrity.signature");
    } else if (integrity.key_id !== null || integrity.signature !== null) {
      throw new Error("sha256 integrity cannot carry a key or signature");
    }
    if (!canonicalValuesEqual(receipt.authority, AUTHORITY)) throw new Error("receipt.authority widens the protocol boundary");
    const requestDigest = digestValue(request).digest;
    if (receipt.request_hash !== requestDigest) throw new Error("receipt.request_hash does not match request");
    for (const product of ["seiche", "undertow", "liquilens"]) {
      if (evidence[product].request_hash !== requestDigest) throw new Error(`evidence.${product}.request_hash does not match request`);
    }
    if (preview.request_hash !== requestDigest) throw new Error("broker_preview.request_hash does not match request");
    if (preview.account_id !== request.agent.account_id) throw new Error("broker_preview.account_id does not match request");
    if (receipt.policy_hash !== digestValue(policy).digest) throw new Error("receipt.policy_hash does not match policy");
    if (request.policy_ref.policy_id !== policy.policy_id || request.policy_ref.version !== policy.version) throw new Error("request.policy_ref does not match embedded policy");
    const digest = digestValue(receiptPayload(receipt)).digest;
    if (receipt.record_hash !== digest) throw new Error("receipt.record_hash does not match receipt content");
    if (receipt.receipt_id !== `trade_safety_${digest.slice(0, 24)}`) throw new Error("receipt.receipt_id does not match receipt content");
    if (authenticated) {
      if (!(hmacKey instanceof Uint8Array) || hmacKey.byteLength === 0) throw new Error("hmacKey is required to authenticate this receipt");
      const expected = createHmac("sha256", hmacKey).update(`${TRADE_SAFETY_HMAC_DOMAIN}${digest}`, "ascii").digest();
      const actual = Buffer.from(integrity.signature, "hex");
      if (!equalBytes(expected, actual)) throw new Error("receipt HMAC signature is invalid");
    } else if (hmacKey !== undefined) {
      throw new Error("hmacKey was supplied for a hash-only receipt");
    }
    if (!canonicalValuesEqual(receipt.decision, expectedDecision(receipt, authenticated))) throw new Error("receipt.decision does not match deterministic policy");
    if (issuedAt < timestamp(request.created_at, "request.created_at")) throw new Error("receipt was evaluated before request.created_at");
    if (issuedAt >= timestamp(request.expires_at, "request.expires_at")) throw new Error("receipt was evaluated after request expiry");
    if (expiresAt > safeReceiptExpiry(receipt)) {
      throw new Error("receipt.expires_at exceeds its request, evidence, preview, or TTL boundary");
    }
    if (request.mode === "live" && receipt.decision.outcome === "pass" && !authenticated) {
      throw new Error("a live pass must be authenticated");
    }
    return Object.freeze({
      authenticated,
      outcome: receipt.decision.outcome,
      enforced: receipt.decision.enforced,
      receiptId: receipt.receipt_id,
      requestHash: requestDigest,
      policyHash: receipt.policy_hash,
      keyId: integrity.key_id,
      expiresAt: receipt.expires_at,
      receipt,
    });
  } catch (error) {
    if (error instanceof TradeSafetyVerificationError) throw error;
    throw new TradeSafetyVerificationError(error instanceof Error ? error.message : String(error), { cause: error });
  }
}

function bindingText(binding, key, { nullable = false } = {}) {
  return text(binding[key], `binding.${key}`, { nullable });
}

function verifyBinding(receipt, binding) {
  exactKeys(binding, "binding", new Set([
    "account_id", "tenant_id", "operator_id", "agent_id", "runtime", "strategy_id",
    "policy_id", "policy_version", "policy_hash", "issuer_name", "issuer_version",
    "issuer_endpoint", "hmac_key_id",
  ]));
  const request = receipt.request;
  const agentFields = ["account_id", "tenant_id", "operator_id", "agent_id", "runtime", "strategy_id"];
  for (const key of agentFields) {
    const expected = bindingText(binding, key, { nullable: key === "strategy_id" });
    if (request.agent[key] !== expected) throw new TradeSafetyOrderBlocked("execution_context_mismatch", "request identity does not match this broker credential lane", { receiptId: receipt.receipt_id });
  }
  if (request.policy_ref.policy_id !== bindingText(binding, "policy_id") || request.policy_ref.version !== bindingText(binding, "policy_version")) throw new TradeSafetyOrderBlocked("policy_identity_mismatch", "request policy identity does not match the operator binding", { receiptId: receipt.receipt_id });
  if (receipt.policy_hash !== sha256(binding.policy_hash, "binding.policy_hash")) throw new TradeSafetyOrderBlocked("policy_hash_mismatch", "receipt policy content does not match the operator binding", { receiptId: receipt.receipt_id });
  if (receipt.issuer.name !== bindingText(binding, "issuer_name") || receipt.issuer.version !== bindingText(binding, "issuer_version") || receipt.issuer.endpoint !== bindingText(binding, "issuer_endpoint")) throw new TradeSafetyOrderBlocked("issuer_mismatch", "receipt issuer does not match the trusted gateway binding", { receiptId: receipt.receipt_id });
  if (receipt.integrity.key_id !== bindingText(binding, "hmac_key_id")) throw new TradeSafetyOrderBlocked("integrity_key_mismatch", "receipt integrity key does not match the operator binding", { receiptId: receipt.receipt_id });
}

export class InMemoryReceiptConsumer {
  #clock;
  #maxEntries;
  #used = new Map();
  #lastNow = null;
  #tail = Promise.resolve();

  constructor({ clock = () => new Date(), maxEntries = 10_000 } = {}) {
    if (typeof clock !== "function") throw new TypeError("clock must be callable");
    if (!Number.isSafeInteger(maxEntries) || maxEntries <= 0) throw new TypeError("maxEntries must be a positive safe integer");
    this.#clock = clock;
    this.#maxEntries = maxEntries;
  }

  async consume({ receiptId, expiresAt }) {
    const claim = async () => {
      const clockValue = this.#clock();
      if (!(clockValue instanceof Date) || !Number.isFinite(clockValue.getTime())) return false;
      const now = clockValue.getTime();
      if (this.#lastNow !== null && now < this.#lastNow) return false;
      this.#lastNow = now;
      for (const [id, deadline] of this.#used) if (deadline <= now) this.#used.delete(id);
      const expiry = timestamp(expiresAt, "expiresAt");
      if (expiry <= now || this.#used.has(receiptId) || this.#used.size >= this.#maxEntries) return false;
      this.#used.set(receiptId, expiry);
      return true;
    };
    const result = this.#tail.then(claim, claim);
    this.#tail = result.then(() => undefined, () => undefined);
    return result;
  }
}

export class PaperTradeSafetyOrderGateway {
  #binding;
  #clock;
  #consumer;
  #hmacKey;
  #submitOrder;

  constructor(submitOrder, { binding, receiptConsumer, hmacKey, clock = () => new Date() }) {
    if (typeof submitOrder !== "function") throw new TypeError("submitOrder must be callable");
    if (!receiptConsumer || typeof receiptConsumer.consume !== "function") throw new TypeError("receiptConsumer.consume must be callable");
    if (!(hmacKey instanceof Uint8Array) || hmacKey.byteLength === 0) throw new TypeError("the paper gateway requires a non-empty HMAC key");
    if (typeof clock !== "function") throw new TypeError("clock must be callable");
    this.#submitOrder = submitOrder;
    this.#binding = binding;
    this.#consumer = receiptConsumer;
    this.#hmacKey = new Uint8Array(hmacKey);
    this.#clock = clock;
  }

  async submit(requestUtf8, receiptUtf8) {
    let request;
    try {
      const parsed = parseJsonUtf8(requestUtf8, "request");
      if (isJsonObject(parsed) && parsed.mode === "live") {
        throw new TradeSafetyOrderBlocked("mode_not_supported", "the reference order guard is paper-only; live routing is held");
      }
      request = validateRequest(parsed);
    } catch (error) {
      if (error instanceof TradeSafetyOrderBlocked) throw error;
      throw new TradeSafetyOrderBlocked("request_invalid", error instanceof Error ? error.message : String(error));
    }
    if (request.mode !== "paper") throw new TradeSafetyOrderBlocked("mode_not_supported", "the reference order guard is paper-only; live routing is held");
    const clockValue = this.#clock();
    if (!(clockValue instanceof Date) || !Number.isFinite(clockValue.getTime())) throw new TradeSafetyOrderBlocked("clock_unavailable", "order-gateway clock must return a valid Date");
    let verified;
    try {
      verified = verifyTradeSafetyReceipt(receiptUtf8, { evaluatedAt: clockValue, hmacKey: this.#hmacKey });
    } catch (error) {
      throw new TradeSafetyOrderBlocked("receipt_invalid", error instanceof Error ? error.message : String(error));
    }
    if (verified.requestHash !== digestValue(request).digest || !canonicalValuesEqual(request, verified.receipt.request)) throw new TradeSafetyOrderBlocked("request_mismatch", "receipt does not bind the exact proposed request", { receiptId: verified.receiptId });
    verifyBinding(verified.receipt, this.#binding);
    if (verified.enforced !== true) throw new TradeSafetyOrderBlocked("decision_not_enforced", "receipt was produced for an observation-only workflow", { outcome: verified.outcome, receiptId: verified.receiptId });
    if (verified.outcome !== "pass") throw new TradeSafetyOrderBlocked(`decision_${verified.outcome}`, "operator policy did not authorize this exact paper order", { outcome: verified.outcome, receiptId: verified.receiptId });
    let consumed;
    try {
      consumed = await this.#consumer.consume({ receiptId: verified.receiptId, requestHash: verified.requestHash, expiresAt: verified.expiresAt });
    } catch (error) {
      throw new TradeSafetyOrderBlocked("receipt_consumer_unavailable", "receipt replay protection could not be reached", { outcome: verified.outcome, receiptId: verified.receiptId, cause: error });
    }
    if (consumed !== true) throw new TradeSafetyOrderBlocked("receipt_replay", "receipt was expired, already claimed, or could not be atomically claimed", { outcome: verified.outcome, receiptId: verified.receiptId });
    const authorization = Object.freeze({
      requestJson: new TextDecoder("utf-8", { fatal: true }).decode(requestUtf8),
      receiptId: verified.receiptId,
      requestHash: verified.requestHash,
      authenticated: true,
      binding: Object.freeze({ ...this.#binding }),
    });
    return this.#submitOrder(authorization);
  }
}
