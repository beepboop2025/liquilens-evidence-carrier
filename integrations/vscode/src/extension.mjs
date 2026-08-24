import * as vscode from "vscode";

import { verifyCarrierText } from "../../../browser/verifier.mjs";
import {
  failureDetails,
  isCarrierFilename,
  successMessage,
} from "./diagnostics.mjs";
import { createLatestOnlyGuard } from "./latest.mjs";

const SOURCE = "LiquiLens Evidence";

function enabled(setting) {
  return vscode.workspace
    .getConfiguration("liquilensEvidence")
    .get(setting, true);
}

function diagnosticRange(document) {
  const firstLine = document.lineAt(0);
  return new vscode.Range(0, 0, 0, firstLine.text.length);
}

export function activate(context) {
  const diagnostics =
    vscode.languages.createDiagnosticCollection("liquilensEvidence");
  const verificationGuard = createLatestOnlyGuard();

  async function verifyDocument(document, interactive = false) {
    const documentKey = document.uri.toString();
    if (!isCarrierFilename(document.fileName) && !interactive) {
      verificationGuard.invalidate(documentKey);
      diagnostics.delete(document.uri);
      return;
    }

    const ticket = verificationGuard.begin(documentKey, document.version);
    let result;
    try {
      result = await verifyCarrierText(document.getText());
    } catch {
      result = {
        ok: false,
        error: {
          code: "runtime",
          path: "runtime",
          message: "verification could not complete",
        },
      };
    }
    if (!verificationGuard.isCurrent(ticket, document.version)) return;

    const failure = failureDetails(result);
    if (failure === null) {
      diagnostics.delete(document.uri);
      if (interactive) {
        await vscode.window.showInformationMessage(successMessage(result));
      }
      return;
    }

    const diagnostic = new vscode.Diagnostic(
      diagnosticRange(document),
      failure.message,
      vscode.DiagnosticSeverity.Error,
    );
    diagnostic.source = SOURCE;
    diagnostic.code = failure.code;
    diagnostics.set(document.uri, [diagnostic]);
    if (interactive) {
      await vscode.window.showErrorMessage(
        `LiquiLens verification failed: ${failure.message}`,
      );
    }
  }

  const verifyCommand = vscode.commands.registerCommand(
    "liquilensEvidence.verifyCarrier",
    async () => {
      const document = vscode.window.activeTextEditor?.document;
      if (document === undefined) {
        await vscode.window.showWarningMessage(
          "Open an Evidence Carrier JSON file before verification.",
        );
        return;
      }
      await verifyDocument(document, true);
    },
  );

  const openListener = vscode.workspace.onDidOpenTextDocument((document) => {
    if (enabled("validateOnOpen")) void verifyDocument(document);
  });
  const saveListener = vscode.workspace.onDidSaveTextDocument((document) => {
    if (enabled("validateOnSave")) void verifyDocument(document);
  });
  const closeListener = vscode.workspace.onDidCloseTextDocument((document) => {
    verificationGuard.invalidate(document.uri.toString());
    diagnostics.delete(document.uri);
  });
  const configurationListener = vscode.workspace.onDidChangeConfiguration(
    (event) => {
      if (!event.affectsConfiguration("liquilensEvidence")) return;
      for (const document of vscode.workspace.textDocuments) {
        if (enabled("validateOnOpen")) void verifyDocument(document);
        else {
          verificationGuard.invalidate(document.uri.toString());
          diagnostics.delete(document.uri);
        }
      }
    },
  );

  context.subscriptions.push(
    diagnostics,
    verifyCommand,
    openListener,
    saveListener,
    closeListener,
    configurationListener,
  );

  if (enabled("validateOnOpen")) {
    for (const document of vscode.workspace.textDocuments) {
      void verifyDocument(document);
    }
  }
}

export function deactivate() {}
