export const RELEASE_VERSION = "0.13.6";
export const PROTOCOL_VERSION = "1.0";
export const CANONICALIZATION = "liquilens-hash-tree-v1";
export const FULL_SCHEMA_URL =
  "https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json";
export const REFERENCE_SCHEMA_URL =
  "https://liquilens.in/protocol/liquilens-evidence-carrier-reference-v1.schema.json";

const REFERENCE_SCHEMA = "liquilens.evidence-carrier-reference.v1";
const MAX_BYTES = 1_048_576;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const CARRIER_ID_PATTERN = /^evidence_[0-9a-f]{24}$/u;
const PRODUCTS = new Set(["liquilens", "seiche", "undertow", "palimpsest"]);
const CLAIM_STATUSES = new Set([
  "observed",
  "derived",
  "structural",
  "research",
  "restricted",
  "unavailable",
]);
const RIGHTS_STATUSES = new Set([
  "licensed",
  "allowed",
  "metadata_only",
  "restricted",
  "unknown",
  "blocked",
]);
const RIGHTS_PERMISSIONS = new Set([
  "ingest",
  "derive",
  "display",
  "redistribute",
]);

export class CarrierVerificationError extends Error {
  constructor(path, message, code = "contract") {
    super(`${path}: ${message}`);
    this.name = "CarrierVerificationError";
    this.path = path;
    this.code = code;
  }
}

class NumericLexeme {
  constructor(kind, value) {
    this.kind = kind;
    this.value = value;
    Object.freeze(this);
  }
}

function fail(path, message, code = "contract") {
  throw new CarrierVerificationError(path, message, code);
}

function assertUnicodeScalars(value, path = "JSON string") {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const following = value.charCodeAt(index + 1);
      if (!(following >= 0xdc00 && following <= 0xdfff)) {
        fail(path, "contains an unpaired high surrogate", "json");
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      fail(path, "contains an unpaired low surrogate", "json");
    }
  }
  return value;
}

class LexemeJsonParser {
  constructor(text) {
    this.text = text;
    this.index = 0;
  }

  parse() {
    const value = this.parseValue();
    this.skipWhitespace();
    if (this.index !== this.text.length) {
      fail("JSON", `unexpected trailing content at byte ${this.index}`, "json");
    }
    return value;
  }

  skipWhitespace() {
    while (/[ \t\r\n]/u.test(this.text[this.index] ?? "")) {
      this.index += 1;
    }
  }

  parseValue() {
    this.skipWhitespace();
    const character = this.text[this.index];
    if (character === "{") return this.parseObject();
    if (character === "[") return this.parseArray();
    if (character === "\"") return this.parseString();
    if (character === "t") return this.parseLiteral("true", true);
    if (character === "f") return this.parseLiteral("false", false);
    if (character === "n") return this.parseLiteral("null", null);
    if (character === "-" || (character >= "0" && character <= "9")) {
      return this.parseNumber();
    }
    fail("JSON", `unexpected token at byte ${this.index}`, "json");
  }

  parseLiteral(token, value) {
    if (this.text.slice(this.index, this.index + token.length) !== token) {
      fail("JSON", `invalid literal at byte ${this.index}`, "json");
    }
    this.index += token.length;
    return value;
  }

  parseString() {
    const start = this.index;
    this.index += 1;
    while (this.index < this.text.length) {
      const character = this.text[this.index];
      if (character === "\"") {
        this.index += 1;
        let value;
        try {
          value = JSON.parse(this.text.slice(start, this.index));
        } catch {
          fail("JSON", `invalid string at byte ${start}`, "json");
        }
        return assertUnicodeScalars(value);
      }
      if (character === "\\") {
        this.index += 1;
        const escaped = this.text[this.index];
        if (escaped === "u") {
          const hex = this.text.slice(this.index + 1, this.index + 5);
          if (!/^[0-9a-fA-F]{4}$/u.test(hex)) {
            fail("JSON", `invalid Unicode escape at byte ${this.index}`, "json");
          }
          this.index += 5;
          continue;
        }
        if (!"\"\\/bfnrt".includes(escaped ?? "")) {
          fail("JSON", `invalid string escape at byte ${this.index}`, "json");
        }
        this.index += 1;
        continue;
      }
      if (character.charCodeAt(0) < 0x20) {
        fail("JSON", `unescaped control character at byte ${this.index}`, "json");
      }
      this.index += 1;
    }
    fail("JSON", "unterminated string", "json");
  }

  parseNumber() {
    const remainder = this.text.slice(this.index);
    const match = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/u.exec(
      remainder,
    );
    if (match === null) {
      fail("JSON", `invalid number at byte ${this.index}`, "json");
    }
    const lexeme = match[0];
    this.index += lexeme.length;
    if (/[.eE]/u.test(lexeme)) {
      const number = Number(lexeme);
      if (!Number.isFinite(number)) {
        fail("JSON", "float is not finite float64", "json");
      }
      return new NumericLexeme("float64", number);
    }
    return new NumericLexeme("integer", BigInt(lexeme).toString(10));
  }

  parseArray() {
    this.index += 1;
    const result = [];
    this.skipWhitespace();
    if (this.text[this.index] === "]") {
      this.index += 1;
      return result;
    }
    while (true) {
      result.push(this.parseValue());
      this.skipWhitespace();
      const separator = this.text[this.index];
      this.index += 1;
      if (separator === "]") return result;
      if (separator !== ",") {
        fail("JSON", `expected array separator at byte ${this.index - 1}`, "json");
      }
    }
  }

  parseObject() {
    this.index += 1;
    const result = Object.create(null);
    this.skipWhitespace();
    if (this.text[this.index] === "}") {
      this.index += 1;
      return result;
    }
    while (true) {
      this.skipWhitespace();
      if (this.text[this.index] !== "\"") {
        fail("JSON", `expected object key at byte ${this.index}`, "json");
      }
      const key = this.parseString();
      if (Object.hasOwn(result, key)) {
        fail("JSON", `duplicate object key ${JSON.stringify(key)}`, "json");
      }
      this.skipWhitespace();
      if (this.text[this.index] !== ":") {
        fail("JSON", `expected object colon at byte ${this.index}`, "json");
      }
      this.index += 1;
      result[key] = this.parseValue();
      this.skipWhitespace();
      const separator = this.text[this.index];
      this.index += 1;
      if (separator === "}") return result;
      if (separator !== ",") {
        fail("JSON", `expected object separator at byte ${this.index - 1}`, "json");
      }
    }
  }
}

function compareUnicodeScalars(left, right) {
  const leftPoints = Array.from(left, (value) => value.codePointAt(0));
  const rightPoints = Array.from(right, (value) => value.codePointAt(0));
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    if (leftPoints[index] !== rightPoints[index]) {
      return leftPoints[index] - rightPoints[index];
    }
  }
  return leftPoints.length - rightPoints.length;
}

function float64Hex(value) {
  const normalized = Object.is(value, -0) || value === 0 ? 0 : value;
  const buffer = new ArrayBuffer(8);
  new DataView(buffer).setFloat64(0, normalized, false);
  return Array.from(
    new Uint8Array(buffer),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
}

function hashTree(value) {
  if (value instanceof NumericLexeme) {
    return value.kind === "integer"
      ? ["integer", value.value]
      : ["float64", float64Hex(value.value)];
  }
  if (value === null) return ["null"];
  if (typeof value === "boolean") return ["boolean", value];
  if (typeof value === "string") return ["string", assertUnicodeScalars(value)];
  if (Array.isArray(value)) return ["array", value.map(hashTree)];
  if (isObject(value)) {
    const keys = Object.keys(value).sort(compareUnicodeScalars);
    return ["object", keys.map((key) => [key, hashTree(value[key])])];
  }
  fail("carrier", `unsupported canonical value type ${typeof value}`, "canonical");
}

async function digestTree(tree) {
  if (globalThis.crypto?.subtle === undefined) {
    fail("runtime", "Web Crypto SHA-256 is unavailable", "runtime");
  }
  const canonical = JSON.stringify(tree);
  const digestBytes = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonical),
  );
  const digest = Array.from(
    new Uint8Array(digestBytes),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
  return { canonical, digest };
}

export async function canonicalHashFromJsonText(text) {
  const value = new LexemeJsonParser(text).parse();
  const tree = hashTree(value);
  return { tree, ...(await digestTree(tree)) };
}

function isObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    !(value instanceof NumericLexeme)
  );
}

function requireObject(value, path) {
  if (!isObject(value)) fail(path, "must be an object");
  return value;
}

function exactKeys(value, path, required, optional = []) {
  requireObject(value, path);
  const requiredSet = new Set(required);
  const allowed = new Set([...required, ...optional]);
  const keys = Object.keys(value);
  const missing = [...requiredSet].filter((key) => !Object.hasOwn(value, key));
  const extra = keys.filter((key) => !allowed.has(key));
  if (missing.length > 0) fail(path, `missing fields: ${missing.sort().join(", ")}`);
  if (extra.length > 0) fail(path, `unsupported fields: ${extra.sort().join(", ")}`);
}

function requireString(value, path) {
  if (typeof value !== "string" || value.trim().length === 0) {
    fail(path, "must be a non-blank string");
  }
  return value;
}

function requireSha256(value, path) {
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    fail(path, "must be lowercase SHA-256");
  }
  return value;
}

function requireHttps(value, path) {
  requireString(value, path);
  if (!value.startsWith("https://")) fail(path, "must start with https://");
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    fail(path, "must be a valid HTTPS URL");
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.hostname.length === 0 ||
    parsed.username.length > 0 ||
    parsed.password.length > 0
  ) {
    fail(path, "must be an HTTPS URL without user information");
  }
  return value;
}

function requireStringArray(value, path, allowed = null) {
  if (!Array.isArray(value) || value.length === 0) {
    fail(path, "must be a non-empty string array");
  }
  const strings = value.map((item, index) => requireString(item, `${path}[${index}]`));
  if (new Set(strings).size !== strings.length) fail(path, "must not contain duplicates");
  if (allowed !== null && strings.some((item) => !allowed.has(item))) {
    fail(path, "contains an unsupported value");
  }
  return strings;
}

function requireNullableString(value, path) {
  if (value === null) return null;
  return requireString(value, path);
}

function timestampParts(value, path) {
  if (typeof value !== "string") fail(path, "must be a UTC timestamp ending in Z");
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?Z$/u.exec(
    value,
  );
  if (match === null) fail(path, "must be an RFC 3339 UTC timestamp ending in Z");
  const [year, month, day, hour, minute, second] = match
    .slice(1, 7)
    .map((item) => Number(item));
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) {
    fail(path, "contains an invalid date or time component");
  }
  const instant = new Date(0);
  instant.setUTCFullYear(year, month - 1, day);
  instant.setUTCHours(hour, minute, second, 0);
  if (
    instant.getUTCFullYear() !== year ||
    instant.getUTCMonth() !== month - 1 ||
    instant.getUTCDate() !== day ||
    instant.getUTCHours() !== hour ||
    instant.getUTCMinutes() !== minute ||
    instant.getUTCSeconds() !== second
  ) {
    fail(path, "contains an invalid calendar date");
  }
  return {
    seconds: BigInt(Math.floor(instant.getTime() / 1000)),
    fraction: match[7] ?? "",
  };
}

function compareTimestamp(left, right) {
  if (left.seconds < right.seconds) return -1;
  if (left.seconds > right.seconds) return 1;
  const length = Math.max(left.fraction.length, right.fraction.length);
  const leftFraction = left.fraction.padEnd(length, "0");
  const rightFraction = right.fraction.padEnd(length, "0");
  return leftFraction < rightFraction ? -1 : leftFraction > rightFraction ? 1 : 0;
}

function validateProducer(value) {
  const producer = requireObject(value, "producer");
  exactKeys(producer, "producer", ["name", "version", "endpoint"]);
  if (!PRODUCTS.has(producer.name)) fail("producer.name", "is not a fleet product");
  requireString(producer.version, "producer.version");
  requireHttps(producer.endpoint, "producer.endpoint");
}

function validateSubject(value) {
  const subject = requireObject(value, "subject");
  exactKeys(subject, "subject", ["kind", "name", "identifiers"]);
  requireString(subject.kind, "subject.kind");
  requireString(subject.name, "subject.name");
  const identifiers = requireObject(subject.identifiers, "subject.identifiers");
  if (Object.keys(identifiers).length === 0) fail("subject.identifiers", "must not be empty");
  for (const [key, item] of Object.entries(identifiers)) {
    if (key.trim().length === 0 || /\s/u.test(key)) {
      fail("subject.identifiers", "keys must be non-blank tokens");
    }
    requireString(item, `subject.identifiers.${key}`);
  }
}

function validateClaim(value) {
  const claim = requireObject(value, "claim");
  exactKeys(claim, "claim", ["kind", "summary", "status"]);
  requireString(claim.kind, "claim.kind");
  requireString(claim.summary, "claim.summary");
  if (!CLAIM_STATUSES.has(claim.status)) fail("claim.status", "is unsupported");
}

function validateClocks(value) {
  const clocks = requireObject(value, "clocks");
  exactKeys(clocks, "clocks", ["event_time", "knowledge_time", "as_of"], ["expires_at"]);
  const eventTime = timestampParts(clocks.event_time, "clocks.event_time");
  const knowledgeTime = timestampParts(clocks.knowledge_time, "clocks.knowledge_time");
  const asOf = timestampParts(clocks.as_of, "clocks.as_of");
  if (compareTimestamp(eventTime, knowledgeTime) > 0 || compareTimestamp(knowledgeTime, asOf) > 0) {
    fail("clocks", "must satisfy event_time <= knowledge_time <= as_of");
  }
  let expiresAt = null;
  if (Object.hasOwn(clocks, "expires_at")) {
    expiresAt = timestampParts(clocks.expires_at, "clocks.expires_at");
    if (compareTimestamp(expiresAt, knowledgeTime) <= 0) {
      fail("clocks.expires_at", "must follow knowledge_time");
    }
  }
  return { eventTime, knowledgeTime, asOf, expiresAt };
}

function validateSources(value, clocks) {
  if (!Array.isArray(value) || value.length === 0) fail("sources", "must be a non-empty array");
  const sourceIds = new Set();
  value.forEach((item, index) => {
    const path = `sources[${index}]`;
    const source = requireObject(item, path);
    exactKeys(source, path, [
      "source_id",
      "publisher",
      "title",
      "url",
      "retrieved_at",
      "content_sha256",
    ]);
    const sourceId = requireString(source.source_id, `${path}.source_id`);
    if (sourceIds.has(sourceId)) fail("sources", "source_id values must be unique");
    sourceIds.add(sourceId);
    requireString(source.publisher, `${path}.publisher`);
    requireString(source.title, `${path}.title`);
    requireHttps(source.url, `${path}.url`);
    const retrievedAt = timestampParts(source.retrieved_at, `${path}.retrieved_at`);
    if (compareTimestamp(retrievedAt, clocks.eventTime) < 0) {
      fail(`${path}.retrieved_at`, "cannot precede event_time");
    }
    if (compareTimestamp(retrievedAt, clocks.knowledgeTime) > 0) {
      fail(`${path}.retrieved_at`, "cannot follow knowledge_time");
    }
    requireSha256(source.content_sha256, `${path}.content_sha256`);
  });
}

function validateRights(value) {
  const rights = requireObject(value, "rights");
  exactKeys(rights, "rights", [
    "status",
    "permissions",
    "license",
    "license_url",
    "attribution",
    "jurisdictions",
  ]);
  if (!RIGHTS_STATUSES.has(rights.status)) fail("rights.status", "is unsupported");
  requireStringArray(rights.permissions, "rights.permissions", RIGHTS_PERMISSIONS);
  requireStringArray(rights.jurisdictions, "rights.jurisdictions");
  requireNullableString(rights.license, "rights.license");
  if (rights.license_url !== null) requireHttps(rights.license_url, "rights.license_url");
  requireNullableString(rights.attribution, "rights.attribution");
}

function validateAuthority(value) {
  const authority = requireObject(value, "authority");
  exactKeys(authority, "authority", [
    "financial_authority",
    "can_execute",
    "can_recommend",
    "is_credit_rating",
  ]);
  if (
    authority.financial_authority !== "none" ||
    authority.can_execute !== false ||
    authority.can_recommend !== false ||
    authority.is_credit_rating !== false
  ) {
    fail("authority", "must retain the all-false financial-authority boundary");
  }
}

function validateCommon(carrier) {
  validateProducer(carrier.producer);
  validateSubject(carrier.subject);
  validateClaim(carrier.claim);
  const clocks = validateClocks(carrier.clocks);
  validateSources(carrier.sources, clocks);
  validateRights(carrier.rights);
  validateAuthority(carrier.authority);
  return clocks;
}

function omitFields(value, fieldNames) {
  const result = Object.create(null);
  for (const key of Object.keys(value)) {
    if (!fieldNames.has(key)) result[key] = value[key];
  }
  return result;
}

function policyAtDeclaredAsOf(carrier, clocks) {
  let disposition = "full";
  const reasons = [];
  const metadataOnly = (reason) => {
    if (disposition === "full") disposition = "metadata_only";
    reasons.push(reason);
  };
  const reject = (reason) => {
    disposition = "reject";
    reasons.push(reason);
  };
  if (["restricted", "unknown", "blocked"].includes(carrier.rights.status)) {
    reject(`rights_${carrier.rights.status}`);
  } else {
    if (carrier.rights.status === "metadata_only") metadataOnly("rights_metadata_only");
    if (!carrier.rights.permissions.includes("redistribute")) {
      metadataOnly("redistribution_not_permitted");
    }
    if (["licensed", "allowed"].includes(carrier.rights.status)) {
      if (carrier.rights.license === null && carrier.rights.license_url === null) {
        metadataOnly("rights_license_missing");
      }
      if (carrier.rights.attribution === null) metadataOnly("rights_attribution_missing");
    }
  }
  if (["restricted", "unavailable"].includes(carrier.claim.status)) {
    metadataOnly(`claim_${carrier.claim.status}`);
  }
  if (clocks.expiresAt !== null && compareTimestamp(clocks.expiresAt, clocks.asOf) <= 0) {
    metadataOnly("evidence_expired");
  }
  return { disposition, reasons: [...new Set(reasons)] };
}

function baseResult(carrier, kind, schemaUrl, proofLevel, clocks) {
  return {
    ok: true,
    kind,
    proofLevel,
    releaseVersion: RELEASE_VERSION,
    protocolVersion: PROTOCOL_VERSION,
    schemaUrl,
    canonicalization: carrier.canonicalization,
    carrierId: carrier.carrier_id,
    recordHash: carrier.record_hash,
    producer: `${carrier.producer.name} ${carrier.producer.version}`,
    subject: carrier.subject.name,
    claim: carrier.claim.summary,
    rightsStatus: carrier.rights.status,
    permissions: [...carrier.rights.permissions],
    authority: carrier.authority.financial_authority,
    clocks: {
      eventTime: carrier.clocks.event_time,
      knowledgeTime: carrier.clocks.knowledge_time,
      asOf: carrier.clocks.as_of,
      expiresAt: carrier.clocks.expires_at ?? null,
    },
    _clockParts: clocks,
  };
}

async function verifyFullCarrier(carrier) {
  exactKeys(carrier, "carrier", [
    "schema_version",
    "canonicalization",
    "carrier_id",
    "record_hash",
    "producer",
    "subject",
    "claim",
    "clocks",
    "sources",
    "rights",
    "authority",
    "payload",
    "extensions",
  ]);
  if (carrier.schema_version !== PROTOCOL_VERSION) {
    fail("schema_version", `must be ${PROTOCOL_VERSION}`);
  }
  if (carrier.canonicalization !== CANONICALIZATION) {
    fail("canonicalization", `must be ${CANONICALIZATION}`);
  }
  if (typeof carrier.carrier_id !== "string" || !CARRIER_ID_PATTERN.test(carrier.carrier_id)) {
    fail("carrier_id", "has an invalid shape");
  }
  requireSha256(carrier.record_hash, "record_hash");
  const clocks = validateCommon(carrier);
  requireObject(carrier.payload, "payload");
  requireObject(carrier.extensions, "extensions");

  const payload = omitFields(carrier, new Set(["carrier_id", "record_hash"]));
  const { digest } = await digestTree(hashTree(payload));
  if (carrier.record_hash !== digest) {
    fail("record_hash", "does not match the canonical carrier payload", "identity");
  }
  if (carrier.carrier_id !== `evidence_${digest.slice(0, 24)}`) {
    fail("carrier_id", "does not match record_hash", "identity");
  }
  const result = baseResult(carrier, "full", FULL_SCHEMA_URL, "exact", clocks);
  const policy = policyAtDeclaredAsOf(carrier, clocks);
  return {
    ...result,
    computedHash: digest,
    disclosureAtDeclaredAsOf: policy.disposition,
    reasonCodes: policy.reasons,
    checks: [
      ["contract", "Exact full-carrier field set", "Known v1 fields, types, enums, and URLs"],
      ["identity", "Content identity recomputed", "SHA-256 matches the canonical hash tree"],
      ["binding", "Carrier ID bound", "ID prefix matches the first 24 hash characters"],
      ["clocks", "Clock order preserved", "event ≤ knowledge ≤ as-of; source retrieval bounded"],
      ["rights", "Rights vocabulary bounded", `Declared disposition at as-of: ${policy.disposition}`],
      ["authority", "Financial authority absent", "Execution, recommendation, and rating flags are false"],
    ].map(([id, label, detail]) => ({ id, label, detail, status: "pass" })),
  };
}

function verifyReferenceCarrier(carrier) {
  exactKeys(carrier, "reference", [
    "schema",
    "carrier_id",
    "record_hash",
    "canonicalization",
    "producer",
    "subject",
    "claim",
    "clocks",
    "sources",
    "rights",
    "authority",
    "payload_disclosed",
    "reason_codes",
    "policy_version",
  ]);
  if (carrier.schema !== REFERENCE_SCHEMA) fail("schema", `must be ${REFERENCE_SCHEMA}`);
  if (carrier.canonicalization !== CANONICALIZATION) {
    fail("canonicalization", `must be ${CANONICALIZATION}`);
  }
  if (typeof carrier.carrier_id !== "string" || !CARRIER_ID_PATTERN.test(carrier.carrier_id)) {
    fail("carrier_id", "has an invalid shape");
  }
  requireSha256(carrier.record_hash, "record_hash");
  if (carrier.carrier_id !== `evidence_${carrier.record_hash.slice(0, 24)}`) {
    fail("carrier_id", "does not match the preserved record_hash", "identity");
  }
  const clocks = validateCommon(carrier);
  if (carrier.payload_disclosed !== false) fail("payload_disclosed", "must be false");
  const reasonCodes = requireStringArray(carrier.reason_codes, "reason_codes");
  requireString(carrier.policy_version, "policy_version");
  const result = baseResult(carrier, "reference", REFERENCE_SCHEMA_URL, "linked", clocks);
  return {
    ...result,
    computedHash: null,
    disclosureAtDeclaredAsOf: "metadata_only",
    reasonCodes,
    policyVersion: carrier.policy_version,
    checks: [
      ["contract", "Exact reference field set", "Known v1 reference fields and linked subcontracts"],
      ["binding", "Preserved identity linked", "Carrier ID prefix matches the declared full-carrier hash"],
      ["redaction", "Payload remains absent", "payload_disclosed is false and no payload field is present"],
      ["clocks", "Clock order preserved", "event ≤ knowledge ≤ as-of; source retrieval bounded"],
      ["rights", "Disclosure reason retained", reasonCodes.join(", ")],
      ["authority", "Financial authority absent", "Execution, recommendation, and rating flags are false"],
    ].map(([id, label, detail]) => ({ id, label, detail, status: "pass" })),
  };
}

export async function verifyCarrierText(text) {
  try {
    if (typeof text !== "string" || text.trim().length === 0) {
      fail("JSON", "paste or choose a carrier first", "input");
    }
    if (new TextEncoder().encode(text).length > MAX_BYTES) {
      fail("JSON", `exceeds the ${MAX_BYTES}-byte carrier limit`, "input");
    }
    const carrier = new LexemeJsonParser(text).parse();
    requireObject(carrier, "carrier");
    if (Object.hasOwn(carrier, "schema_version")) {
      const result = await verifyFullCarrier(carrier);
      delete result._clockParts;
      return result;
    }
    if (Object.hasOwn(carrier, "schema")) {
      const result = verifyReferenceCarrier(carrier);
      delete result._clockParts;
      return result;
    }
    fail("carrier", "is neither a full v1 carrier nor a v1 redacted reference");
  } catch (error) {
    const normalized =
      error instanceof CarrierVerificationError
        ? error
        : new CarrierVerificationError("runtime", "verification could not complete", "runtime");
    return {
      ok: false,
      error: {
        code: normalized.code,
        path: normalized.path,
        message: normalized.message,
      },
    };
  }
}
