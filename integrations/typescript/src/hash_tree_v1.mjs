import { createHash } from "node:crypto";
import { TextDecoder } from "node:util";

export const MAX_ARTIFACT_BYTES = 1_048_576;

export class NumericLexeme {
  constructor(kind, value, source) {
    this.kind = kind;
    this.value = value;
    this.source = source;
    Object.freeze(this);
  }
}

function assertUnicodeScalars(value) {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const following = value.charCodeAt(index + 1);
      if (!(following >= 0xdc00 && following <= 0xdfff)) {
        throw new Error("JSON string contains an unpaired high surrogate");
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new Error("JSON string contains an unpaired low surrogate");
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
      throw new Error(`unexpected trailing JSON at offset ${this.index}`);
    }
    return value;
  }

  skipWhitespace() {
    while (/[ \t\r\n]/u.test(this.text[this.index] ?? "")) this.index += 1;
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
    throw new Error(`unexpected JSON token at offset ${this.index}`);
  }

  parseLiteral(token, value) {
    if (this.text.slice(this.index, this.index + token.length) !== token) {
      throw new Error(`invalid JSON literal at offset ${this.index}`);
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
        return assertUnicodeScalars(JSON.parse(this.text.slice(start, this.index)));
      }
      if (character === "\\") {
        this.index += 1;
        const escaped = this.text[this.index];
        if (escaped === "u") {
          const hex = this.text.slice(this.index + 1, this.index + 5);
          if (!/^[0-9a-fA-F]{4}$/u.test(hex)) {
            throw new Error(`invalid Unicode escape at offset ${this.index}`);
          }
          this.index += 5;
          continue;
        }
        if (!"\"\\/bfnrt".includes(escaped ?? "")) {
          throw new Error(`invalid string escape at offset ${this.index}`);
        }
        this.index += 1;
        continue;
      }
      if (character.charCodeAt(0) < 0x20) {
        throw new Error(`unescaped control character at offset ${this.index}`);
      }
      this.index += 1;
    }
    throw new Error("unterminated JSON string");
  }

  parseNumber() {
    const remainder = this.text.slice(this.index);
    const match = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/u.exec(
      remainder,
    );
    if (match === null) throw new Error(`invalid JSON number at offset ${this.index}`);
    const lexeme = match[0];
    this.index += lexeme.length;
    if (/[.eE]/u.test(lexeme)) {
      const value = Number(lexeme);
      if (!Number.isFinite(value)) throw new Error("JSON float is not finite float64");
      return new NumericLexeme("float64", value, lexeme);
    }
    return new NumericLexeme("integer", BigInt(lexeme).toString(10), lexeme);
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
        throw new Error(`expected array separator at offset ${this.index - 1}`);
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
        throw new Error(`expected object key at offset ${this.index}`);
      }
      const key = this.parseString();
      if (Object.hasOwn(result, key)) throw new Error(`duplicate JSON object key: ${key}`);
      this.skipWhitespace();
      if (this.text[this.index] !== ":") {
        throw new Error(`expected object colon at offset ${this.index}`);
      }
      this.index += 1;
      result[key] = this.parseValue();
      this.skipWhitespace();
      const separator = this.text[this.index];
      this.index += 1;
      if (separator === "}") return result;
      if (separator !== ",") {
        throw new Error(`expected object separator at offset ${this.index - 1}`);
      }
    }
  }
}

function compareUnicodeScalars(left, right) {
  const leftPoints = Array.from(left, (value) => value.codePointAt(0));
  const rightPoints = Array.from(right, (value) => value.codePointAt(0));
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index];
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

export function hashTree(value) {
  if (value instanceof NumericLexeme) {
    return value.kind === "integer"
      ? ["integer", value.value]
      : ["float64", float64Hex(value.value)];
  }
  if (value === null) return ["null"];
  if (typeof value === "boolean") return ["boolean", value];
  if (typeof value === "string") return ["string", assertUnicodeScalars(value)];
  if (Array.isArray(value)) return ["array", value.map(hashTree)];
  if (isJsonObject(value)) {
    const keys = Object.keys(value).sort(compareUnicodeScalars);
    return ["object", keys.map((key) => [key, hashTree(value[key])])];
  }
  throw new Error(`unsupported canonical value type: ${typeof value}`);
}

export function digestValue(value) {
  const tree = hashTree(value);
  const canonicalUtf8 = JSON.stringify(tree);
  const digest = createHash("sha256").update(canonicalUtf8, "utf8").digest("hex");
  return { tree, canonicalUtf8, digest };
}

export function isJsonObject(value) {
  return value !== null && !Array.isArray(value) && typeof value === "object" && !(value instanceof NumericLexeme);
}

export function parseJsonUtf8(input, label = "artifact") {
  if (!(input instanceof Uint8Array)) {
    throw new TypeError(`${label} must be supplied as raw UTF-8 bytes`);
  }
  if (input.byteLength === 0) throw new Error(`${label} must not be empty`);
  if (input.byteLength > MAX_ARTIFACT_BYTES) {
    throw new Error(`${label} exceeds ${MAX_ARTIFACT_BYTES} raw bytes`);
  }
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(input);
  } catch (error) {
    throw new Error(`${label} is not valid UTF-8`, { cause: error });
  }
  return new LexemeJsonParser(text).parse();
}

export function omitObjectFields(value, fieldNames) {
  if (!isJsonObject(value)) throw new Error("artifact root must be a JSON object");
  const result = Object.create(null);
  for (const key of Object.keys(value)) {
    if (!fieldNames.has(key)) result[key] = value[key];
  }
  return result;
}

export function canonicalValuesEqual(left, right) {
  return digestValue(left).canonicalUtf8 === digestValue(right).canonicalUtf8;
}

export function numberValue(value, fieldName) {
  if (!(value instanceof NumericLexeme)) throw new Error(`${fieldName} must be numeric`);
  const number = value.kind === "integer" ? Number(value.value) : value.value;
  if (!Number.isFinite(number)) throw new Error(`${fieldName} is outside finite float64`);
  return number;
}
