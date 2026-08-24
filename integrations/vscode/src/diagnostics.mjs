const CARRIER_FILENAME = /(?:^|\/)[^/]*\.(?:evidence|carrier)\.json$/u;

export function isCarrierFilename(filename) {
  if (typeof filename !== "string") return false;
  return CARRIER_FILENAME.test(filename.replaceAll("\\", "/"));
}

export function failureDetails(result) {
  if (result?.ok === true) return null;
  const error = result?.error ?? {};
  const code =
    typeof error.code === "string" && error.code.length > 0
      ? error.code
      : "runtime";
  const path =
    typeof error.path === "string" && error.path.length > 0
      ? error.path
      : "carrier";
  const message =
    typeof error.message === "string" && error.message.length > 0
      ? error.message
      : "verification could not complete";
  const prefix = `${path}: `;
  return {
    code: `liquilens-${code}`,
    message: message.startsWith(prefix) ? message : `${prefix}${message}`,
  };
}

export function successMessage(result) {
  if (result?.ok !== true) return "LiquiLens verification failed.";
  const proof = result.proofLevel === "exact" ? "exact" : "linked";
  return [
    `LiquiLens verified ${proof} evidence`,
    result.carrierId,
    `disposition ${result.disclosureAtDeclaredAsOf}`,
  ].join(" · ");
}
