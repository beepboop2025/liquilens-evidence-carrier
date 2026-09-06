"""Operator STOP checks around the pinned Alpaca paper adapter, version 0.1.0.

This subclass deliberately couples to the adapter's protected account-check and
authorized-submit hooks. Its pipeline tests must pass before upgrading that pin.
All account, endpoint, receipt, expiry and durable replay enforcement remains in
the original adapter. The guard never clears a reserved intent or receipt claim.

STOP prevents a new submission at these boundaries. It cannot recall work after
the adapter begins submission or cancel an order already accepted by the broker.
Read-only reconciliation remains available while STOP is present.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from liquilens_alpaca_paper import (
    AlpacaPaperSubmission,
    AlpacaPaperTradeSafetyGateway,
)
from liquilens_evidence import TradeSafetyOrderAuthorization


class OperatorPaperSubmissionStopped(RuntimeError):
    """An operator STOP prevented SDK submission; durable state is retained."""


class OperatorPaperTradeSafetyGateway(AlpacaPaperTradeSafetyGateway):
    """Add local stop checks without replacing the existing paper order guard."""

    def __init__(self, *, state_dir: Path, **adapter_options: Any) -> None:
        if not state_dir.is_absolute():
            raise ValueError("operator_state_directory_must_be_absolute")
        self._operator_state_dir = state_dir
        self._operator_submission_active = False
        super().__init__(**adapter_options)

    def _check_operator_stop(self) -> None:
        stop = self._operator_state_dir / "STOP"
        if stop.exists() or stop.is_symlink():
            raise OperatorPaperSubmissionStopped(
                "operator_stop_file_present_before_submission"
            )

    def submit(
        self, proposed_request: Mapping[str, Any], receipt: Mapping[str, Any]
    ) -> AlpacaPaperSubmission:
        self._check_operator_stop()
        self._operator_submission_active = True
        try:
            return super().submit(proposed_request, receipt)
        finally:
            self._operator_submission_active = False

    def _verify_account_binding(self) -> None:
        super()._verify_account_binding()
        if self._operator_submission_active:
            # The SDK account lookup is blocking network I/O. A STOP arriving
            # during that wait must be checked before consuming the receipt.
            self._check_operator_stop()

    def _submit_authorized(
        self, authorization: TradeSafetyOrderAuthorization
    ) -> AlpacaPaperSubmission:
        # A STOP arriving during receipt verification/claim must be checked
        # before the base adapter begins its durable SDK submission attempt.
        self._check_operator_stop()
        return super()._submit_authorized(authorization)
