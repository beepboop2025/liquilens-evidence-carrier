import { FULL_EXAMPLE, REFERENCE_EXAMPLE } from "./examples.mjs";
import { verifyCarrierText } from "./verifier.mjs";

const elements = {
  input: document.querySelector("#carrier-input"),
  file: document.querySelector("#carrier-file"),
  editor: document.querySelector("#editor-shell"),
  verify: document.querySelector("#verify-button"),
  loadFull: document.querySelector("#load-full"),
  loadReference: document.querySelector("#load-reference"),
  clear: document.querySelector("#clear-button"),
  status: document.querySelector("#verification-status"),
  statusTitle: document.querySelector("#status-title"),
  statusDetail: document.querySelector("#status-detail"),
  error: document.querySelector("#error-message"),
  proof: document.querySelector("#proof-output"),
  hashRail: document.querySelector("#hash-rail"),
  hashCaption: document.querySelector("#hash-caption"),
  checks: document.querySelector("#checks-list"),
  facts: document.querySelector("#facts-list"),
  mode: document.querySelector("#proof-mode"),
  announcement: document.querySelector("#announcement"),
};

let verificationGeneration = 0;

function setInput(value, shouldFocus = true) {
  elements.input.value = value;
  if (shouldFocus) {
    elements.input.focus();
    elements.input.setSelectionRange(0, 0);
  }
}

function setBusy(isBusy) {
  elements.verify.disabled = isBusy;
  elements.verify.textContent = isBusy ? "Verifying…" : "Verify locally";
  elements.status.dataset.state = isBusy ? "working" : elements.status.dataset.state;
}

function replaceText(element, text) {
  element.replaceChildren(document.createTextNode(text));
}

function renderHash(hash, exact) {
  elements.hashRail.replaceChildren();
  for (let offset = 0; offset < hash.length; offset += 8) {
    const block = document.createElement("span");
    block.className = "hash-block";
    block.textContent = hash.slice(offset, offset + 8);
    elements.hashRail.append(block);
  }
  replaceText(
    elements.hashCaption,
    exact
      ? "64 characters recomputed from the canonical payload"
      : "64-character identity retained from the undisclosed full carrier",
  );
}

function renderChecks(checks) {
  elements.checks.replaceChildren();
  for (const check of checks) {
    const item = document.createElement("li");
    const marker = document.createElement("span");
    marker.className = "check-marker";
    marker.setAttribute("aria-hidden", "true");
    marker.textContent = "✓";
    const copy = document.createElement("span");
    const label = document.createElement("strong");
    label.textContent = check.label;
    const detail = document.createElement("small");
    detail.textContent = check.detail;
    copy.append(label, detail);
    item.append(marker, copy);
    elements.checks.append(item);
  }
}

function renderFacts(result) {
  const facts = [
    ["Carrier", result.carrierId],
    ["Subject", result.subject],
    ["Producer", result.producer],
    ["Schema", result.schemaUrl],
    ["Rights", result.rightsStatus],
    ["Disclosure at declared as-of", result.disclosureAtDeclaredAsOf],
    ["Event time", result.clocks.eventTime],
    ["Knowledge time", result.clocks.knowledgeTime],
    ["As of", result.clocks.asOf],
    ["Authority", result.authority],
  ];
  elements.facts.replaceChildren();
  for (const [term, value] of facts) {
    const group = document.createElement("div");
    const title = document.createElement("dt");
    const description = document.createElement("dd");
    title.textContent = term;
    description.textContent = value;
    group.append(title, description);
    elements.facts.append(group);
  }
}

function renderSuccess(result) {
  const isExact = result.proofLevel === "exact";
  elements.status.dataset.state = "pass";
  replaceText(elements.statusTitle, isExact ? "Exact carrier verified" : "Reference validated");
  replaceText(
    elements.statusDetail,
    isExact
      ? "The v1 contract, canonical hash, carrier ID, clocks, rights, and authority boundary agree."
      : "The v1 redacted-reference contract and preserved identity agree. The hidden payload cannot be re-hashed from a reference alone.",
  );
  elements.error.hidden = true;
  elements.proof.hidden = false;
  elements.mode.textContent = isExact ? "FULL · EXACT PROOF" : "REFERENCE · LINKED PROOF";
  elements.mode.dataset.kind = result.kind;
  renderHash(result.recordHash, isExact);
  renderChecks(result.checks);
  renderFacts(result);
  elements.announcement.textContent = `${elements.statusTitle.textContent}. ${result.carrierId}`;
}

function renderFailure(result) {
  elements.status.dataset.state = "fail";
  replaceText(elements.statusTitle, "Verification stopped");
  replaceText(
    elements.statusDetail,
    "Nothing was uploaded. Correct the local JSON and verify again.",
  );
  elements.error.hidden = false;
  replaceText(elements.error, result.error.message);
  elements.proof.hidden = true;
  elements.announcement.textContent = `Verification stopped. ${result.error.message}`;
}

async function verify() {
  const generation = ++verificationGeneration;
  setBusy(true);
  const result = await verifyCarrierText(elements.input.value);
  if (generation !== verificationGeneration) return;
  setBusy(false);
  if (result.ok) renderSuccess(result);
  else renderFailure(result);
}

async function readFile(file) {
  const generation = ++verificationGeneration;
  if (file.size > 1_048_576) {
    renderFailure({
      error: { message: "JSON: exceeds the 1048576-byte carrier limit" },
    });
    return;
  }
  const contents = await file.text();
  if (generation !== verificationGeneration) return;
  setInput(contents);
  await verify();
}

elements.verify.addEventListener("click", verify);
elements.loadFull.addEventListener("click", () => {
  setInput(FULL_EXAMPLE);
  void verify();
});
elements.loadReference.addEventListener("click", () => {
  setInput(REFERENCE_EXAMPLE);
  void verify();
});
elements.clear.addEventListener("click", () => {
  verificationGeneration += 1;
  setBusy(false);
  elements.input.value = "";
  elements.proof.hidden = true;
  elements.error.hidden = true;
  elements.status.dataset.state = "idle";
  replaceText(elements.statusTitle, "Waiting for evidence");
  replaceText(elements.statusDetail, "Paste JSON, choose a file, or load a known-good example.");
  elements.input.focus();
});
elements.file.addEventListener("change", () => {
  const file = elements.file.files?.item(0);
  if (file !== null && file !== undefined) void readFile(file);
  elements.file.value = "";
});
elements.input.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    void verify();
  }
});

for (const eventName of ["dragenter", "dragover"]) {
  elements.editor.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.editor.dataset.dragging = "true";
  });
}
for (const eventName of ["dragleave", "drop"]) {
  elements.editor.addEventListener(eventName, (event) => {
    event.preventDefault();
    delete elements.editor.dataset.dragging;
  });
}
elements.editor.addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files.item(0);
  if (file !== null && file !== undefined) void readFile(file);
});

setInput(FULL_EXAMPLE, false);
void verify();
