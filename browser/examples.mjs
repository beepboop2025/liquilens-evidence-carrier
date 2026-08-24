export const FULL_EXAMPLE = String.raw`{
  "authority": {
    "can_execute": false,
    "can_recommend": false,
    "financial_authority": "none",
    "is_credit_rating": false
  },
  "canonicalization": "liquilens-hash-tree-v1",
  "carrier_id": "evidence_0c1ba9b4c2184c6b1eb76ebf",
  "claim": {
    "kind": "conformance_example",
    "status": "structural",
    "summary": "Example carrier for the public protocol schema"
  },
  "clocks": {
    "as_of": "2026-08-24T11:26:46Z",
    "event_time": "2026-08-24T11:26:45Z",
    "expires_at": "2030-01-01T00:00:00Z",
    "knowledge_time": "2026-08-24T11:26:45Z"
  },
  "extensions": {},
  "payload": {
    "protocol": "liquilens-evidence-carrier-v1",
    "purpose": "conformance demonstration"
  },
  "producer": {
    "endpoint": "https://liquilens.in/protocol/",
    "name": "liquilens",
    "version": "0.14.0"
  },
  "record_hash": "0c1ba9b4c2184c6b1eb76ebfc8ffd1e85d3e040f0b2d348618292f71d119f376",
  "rights": {
    "attribution": "LiquiLens Evidence Carrier contributors",
    "jurisdictions": [
      "global"
    ],
    "license": "Apache-2.0",
    "license_url": "https://github.com/beepboop2025/liquilens-evidence-carrier/blob/main/LICENSE",
    "permissions": [
      "ingest",
      "derive",
      "display",
      "redistribute"
    ],
    "status": "licensed"
  },
  "schema_version": "1.0",
  "sources": [
    {
      "content_sha256": "7f8494d8470853dc88665ea32c1dccb40cc58c55b07e9267aa28c81f83c1ccd3",
      "publisher": "LiquiLens",
      "retrieved_at": "2026-08-24T11:26:45Z",
      "source_id": "liquilens:evidence-carrier-schema:v1",
      "title": "LiquiLens Evidence Carrier v1 JSON Schema",
      "url": "https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json"
    }
  ],
  "subject": {
    "identifiers": {
      "schema": "liquilens-evidence-carrier-v1"
    },
    "kind": "protocol_artifact",
    "name": "LiquiLens Evidence Carrier v1"
  }
}`;

export const REFERENCE_EXAMPLE = String.raw`{
  "authority": {
    "can_execute": false,
    "can_recommend": false,
    "financial_authority": "none",
    "is_credit_rating": false
  },
  "canonicalization": "liquilens-hash-tree-v1",
  "carrier_id": "evidence_b6628c11e9382595a649bb7c",
  "claim": {
    "kind": "conformance_example",
    "status": "structural",
    "summary": "Example carrier for the public protocol schema"
  },
  "clocks": {
    "as_of": "2026-08-24T11:26:46Z",
    "event_time": "2026-08-24T11:26:45Z",
    "expires_at": "2030-01-01T00:00:00Z",
    "knowledge_time": "2026-08-24T11:26:45Z"
  },
  "payload_disclosed": false,
  "policy_version": "liquilens-evidence-export-strict-v1",
  "producer": {
    "endpoint": "https://liquilens.in/protocol/",
    "name": "liquilens",
    "version": "0.14.0"
  },
  "reason_codes": [
    "redistribution_not_permitted"
  ],
  "record_hash": "b6628c11e9382595a649bb7cf717dd2665cb7034d6fbe50c659c90598d9e38b1",
  "rights": {
    "attribution": "LiquiLens Evidence Carrier contributors",
    "jurisdictions": [
      "global"
    ],
    "license": "Apache-2.0",
    "license_url": "https://github.com/beepboop2025/liquilens-evidence-carrier/blob/main/LICENSE",
    "permissions": [
      "ingest",
      "derive",
      "display"
    ],
    "status": "licensed"
  },
  "schema": "liquilens.evidence-carrier-reference.v1",
  "sources": [
    {
      "content_sha256": "7f8494d8470853dc88665ea32c1dccb40cc58c55b07e9267aa28c81f83c1ccd3",
      "publisher": "LiquiLens",
      "retrieved_at": "2026-08-24T11:26:45Z",
      "source_id": "liquilens:evidence-carrier-schema:v1",
      "title": "LiquiLens Evidence Carrier v1 JSON Schema",
      "url": "https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json"
    }
  ],
  "subject": {
    "identifiers": {
      "schema": "liquilens-evidence-carrier-v1"
    },
    "kind": "protocol_artifact",
    "name": "LiquiLens Evidence Carrier v1"
  }
}`;
