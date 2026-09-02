export type RawUtf8 = Uint8Array;

export interface VerificationOptions {
  evaluatedAt: Date | string;
  hmacKey?: Uint8Array;
}

export interface VerifiedTradeSafetyReceipt {
  readonly authenticated: boolean;
  readonly outcome: "pass" | "limit" | "hold" | "unavailable";
  readonly enforced: boolean;
  readonly receiptId: string;
  readonly requestHash: string;
  readonly policyHash: string;
  readonly keyId: string | null;
  readonly expiresAt: string;
  readonly receipt: unknown;
}

export interface TradeSafetyExecutionBinding {
  account_id: string;
  tenant_id: string;
  operator_id: string;
  agent_id: string;
  runtime: string;
  strategy_id: string | null;
  policy_id: string;
  policy_version: string;
  policy_hash: string;
  issuer_name: string;
  issuer_version: string;
  issuer_endpoint: string;
  hmac_key_id: string;
}

export interface ReceiptClaim {
  receiptId: string;
  requestHash: string;
  expiresAt: string;
}

export interface ReceiptConsumer {
  consume(claim: ReceiptClaim): boolean | Promise<boolean>;
}

export interface TradeSafetyOrderAuthorization {
  readonly requestJson: string;
  readonly receiptId: string;
  readonly requestHash: string;
  readonly authenticated: true;
  readonly binding: Readonly<TradeSafetyExecutionBinding>;
}

export class TradeSafetyVerificationError extends Error {}

export class TradeSafetyOrderBlocked extends TradeSafetyVerificationError {
  readonly reasonCode: string;
  readonly outcome: string | null;
  readonly receiptId: string | null;
}

export function verifyTradeSafetyReceipt(
  receiptUtf8: RawUtf8,
  options: VerificationOptions,
): VerifiedTradeSafetyReceipt;

export class InMemoryReceiptConsumer implements ReceiptConsumer {
  constructor(options?: { clock?: () => Date; maxEntries?: number });
  consume(claim: ReceiptClaim): Promise<boolean>;
}

export class PaperTradeSafetyOrderGateway<T> {
  constructor(
    submitOrder: (authorization: TradeSafetyOrderAuthorization) => T | Promise<T>,
    options: {
      binding: TradeSafetyExecutionBinding;
      receiptConsumer: ReceiptConsumer;
      hmacKey: Uint8Array;
      clock?: () => Date;
    },
  );
  submit(requestUtf8: RawUtf8, receiptUtf8: RawUtf8): Promise<T>;
}

export const TRADE_SAFETY_HMAC_DOMAIN: "liquilens.trade-safety-receipt.v1\n";
