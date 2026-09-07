"""Deterministic BTC paper-trade proposals; never an order authorization.

The timestamps supplied here are *close times of complete bars*. Adapters must
exclude an in-progress bar before calling this module. No account, broker,
network, or evidence-receipt operation is performed by the strategy.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from statistics import fmean, pstdev
from typing import Literal

_SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60
_MAX_BARS = 10_000
_REGIMES = frozenset({"CALM", "EROSION", "STRAIN", "STRESS"})


@dataclass(frozen=True, slots=True)
class MarketBar:
    """An observed complete bar, timestamped at its close in an aware timezone."""

    at: datetime
    close: float


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """Fresh broker observations supplied by the caller, without buying power."""

    cash_usd: float
    equity_usd: float
    btc_notional_usd: float
    daily_pnl_usd: float
    open_orders: int


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Bounded paper settings, with no live-mode or leverage configuration.

    Default proposals use at most the gateway's published $1,000 rung. The
    caller must still match the exact proposed size against current gateway
    capabilities. Smaller remainders are held rather than rounded upward.
    """

    fast_window: int = 5
    slow_window: int = 30
    min_bars: int = 30
    bar_interval_seconds: int = 3600
    max_stale_seconds: int = 7200
    momentum_threshold: float = 0.003
    target_annualized_volatility: float = 0.20
    max_portfolio_exposure: float = 0.10
    order_notional_usd: float = 1000.0
    min_order_notional_usd: float = 1000.0
    max_daily_loss_fraction: float = 0.02
    rebalance_tolerance_fraction: float = 0.01
    max_open_orders: int = 0


@dataclass(frozen=True, slots=True)
class Decision:
    """A candidate for independent evidence and broker gates, never advice."""

    action: Literal["buy", "sell", "hold"]
    notional_usd: float | None = None
    reasons: tuple[str, ...] = ()
    metrics: dict[str, float | int | str] = field(default_factory=dict)


def _finite_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _valid_config(config: StrategyConfig) -> bool:
    integers = (
        config.fast_window,
        config.slow_window,
        config.min_bars,
        config.bar_interval_seconds,
        config.max_stale_seconds,
        config.max_open_orders,
    )
    if any(type(value) is not int for value in integers):
        return False
    if not (
        2 <= config.fast_window < config.slow_window <= 720
        and 30 <= config.min_bars <= _MAX_BARS
        and 60 <= config.bar_interval_seconds <= 86400
        and config.bar_interval_seconds
        <= config.max_stale_seconds
        <= 4 * config.bar_interval_seconds
        and config.max_open_orders == 0
    ):
        return False
    numbers = (
        config.momentum_threshold,
        config.target_annualized_volatility,
        config.max_portfolio_exposure,
        config.order_notional_usd,
        config.min_order_notional_usd,
        config.max_daily_loss_fraction,
        config.rebalance_tolerance_fraction,
    )
    return all(_finite_number(value) for value in numbers) and (
        0 < config.momentum_threshold <= 0.25
        and 0 < config.target_annualized_volatility <= 1
        and 0 < config.max_portfolio_exposure <= 0.25
        and 0 < config.min_order_notional_usd <= config.order_notional_usd <= 10_000
        and 0 < config.max_daily_loss_fraction <= 0.10
        and 0 <= config.rebalance_tolerance_fraction < config.max_portfolio_exposure
    )


def _valid_portfolio(portfolio: PortfolioSnapshot) -> bool:
    numbers = (
        portfolio.cash_usd,
        portfolio.equity_usd,
        portfolio.btc_notional_usd,
        portfolio.daily_pnl_usd,
    )
    return (
        all(_finite_number(value) for value in numbers)
        and portfolio.equity_usd > 0
        and 0 <= portfolio.cash_usd <= portfolio.equity_usd
        and 0 <= portfolio.btc_notional_usd <= portfolio.equity_usd
        and (portfolio.cash_usd / portfolio.equity_usd)
        + (portfolio.btc_notional_usd / portfolio.equity_usd)
        <= 1 + 1e-9
        and type(portfolio.open_orders) is int
        and portfolio.open_orders >= 0
    )


def _hold(reason: str, metrics: dict[str, float | int | str] | None = None) -> Decision:
    return Decision("hold", reasons=(reason,), metrics=metrics or {})


def propose(
    bars: Sequence[MarketBar],
    portfolio: PortfolioSnapshot,
    config: StrategyConfig,
    now: datetime,
    seiche_regime: str | None,
) -> Decision:
    """Return a reproducible, unlevered BTC candidate from complete input bars.

    Positive fast/slow momentum proposes buying toward a volatility-sized
    position. EROSION halves that target; STRAIN/STRESS suppress new exposure.
    Negative momentum may propose reducing an existing position, never a short.
    The daily-loss stop freezes both sides and never orders liquidation.

    A ``buy`` or ``sell`` is only a candidate. It supplies no order permission,
    no evidence-freshness guarantee, and no predicted or backtested return.
    """

    if not isinstance(config, StrategyConfig) or not _valid_config(config):
        return _hold("invalid_config")
    if not _aware(now):
        return _hold("invalid_clock")
    if not isinstance(portfolio, PortfolioSnapshot) or not _valid_portfolio(portfolio):
        return _hold("invalid_portfolio")
    if portfolio.open_orders > config.max_open_orders:
        return _hold("open_orders_pending")
    # Compare the loss with equity at the start of the reported P&L period,
    # rather than shrinking today's permitted loss as equity falls.
    starting_equity = portfolio.equity_usd - portfolio.daily_pnl_usd
    if not math.isfinite(starting_equity) or starting_equity <= 0:
        return _hold("invalid_daily_pnl_basis")
    daily_loss_fraction = max(0.0, -portfolio.daily_pnl_usd / starting_equity)
    risk_metrics: dict[str, float | int | str] = {
        "daily_loss_fraction": daily_loss_fraction,
        "current_exposure_fraction": (
            portfolio.btc_notional_usd / portfolio.equity_usd
        ),
        "exposure_limit_semantics": "entry_target_not_maintained",
        "configured_exposure_ceiling_fraction": config.max_portfolio_exposure,
        "above_configured_exposure_ceiling": int(
            portfolio.btc_notional_usd
            > portfolio.equity_usd * config.max_portfolio_exposure
        ),
        "residual_below_minimum_order": int(
            0 < portfolio.btc_notional_usd < config.min_order_notional_usd
        ),
    }
    if daily_loss_fraction >= config.max_daily_loss_fraction:
        return _hold("daily_loss_stop", risk_metrics)
    if not isinstance(bars, Sequence) or isinstance(bars, (str, bytes)):
        return _hold("invalid_bars", risk_metrics)
    if len(bars) < max(config.min_bars, config.slow_window):
        return _hold("insufficient_bars", risk_metrics)
    if len(bars) > _MAX_BARS:
        return _hold("too_many_bars", risk_metrics)
    previous: datetime | None = None
    for bar in bars:
        if (
            not isinstance(bar, MarketBar)
            or not _aware(bar.at)
            or not _finite_number(bar.close)
            or bar.close <= 0
        ):
            return _hold("invalid_bar", risk_metrics)
        if bar.at > now:
            return _hold("future_bar", risk_metrics)
        if previous is not None:
            elapsed = (bar.at - previous).total_seconds()
            if elapsed <= 0:
                return _hold("non_monotonic_bars", risk_metrics)
            if elapsed != config.bar_interval_seconds:
                return _hold("incomplete_bar_sequence", risk_metrics)
        previous = bar.at
    age = (now - bars[-1].at).total_seconds()
    if age > config.max_stale_seconds:
        return _hold("stale_bars", {**risk_metrics, "latest_bar_age_seconds": age})
    if not isinstance(seiche_regime, str) or seiche_regime not in _REGIMES:
        return _hold("seiche_regime_unavailable", risk_metrics)

    closes = [bar.close for bar in bars[-config.slow_window :]]
    # Normalize before averaging to avoid an overflow from otherwise finite
    # but corrupt extreme prices. Log subtraction also avoids ratio overflow.
    scale = max(closes)
    normalized = [close / scale for close in closes]
    slow_mean = fmean(normalized)
    fast_mean = fmean(normalized[-config.fast_window :])
    momentum = fast_mean / slow_mean - 1.0
    returns = [
        math.log(current) - math.log(previous_close)
        for previous_close, current in pairwise(closes)
    ]
    annualized_volatility = pstdev(returns) * math.sqrt(
        _SECONDS_PER_YEAR / config.bar_interval_seconds
    )
    metrics = {
        **risk_metrics,
        "bar_count": len(bars),
        "latest_bar_age_seconds": age,
        "momentum": momentum,
        "annualized_volatility": annualized_volatility,
        "seiche_regime": seiche_regime,
    }
    if not math.isfinite(annualized_volatility) or annualized_volatility <= 1e-8:
        return _hold("volatility_unavailable", metrics)
    if abs(momentum) < config.momentum_threshold:
        return _hold("momentum_in_neutral_band", metrics)

    target_fraction = min(
        config.max_portfolio_exposure,
        config.target_annualized_volatility / annualized_volatility,
    )
    if seiche_regime == "EROSION":
        target_fraction *= 0.5
    if momentum < 0:
        target_fraction = 0.0
    elif seiche_regime in {"STRAIN", "STRESS"}:
        return _hold("seiche_regime_blocks_new_exposure", metrics)

    target_notional = portfolio.equity_usd * target_fraction
    delta = target_notional - portfolio.btc_notional_usd
    metrics.update(
        target_exposure_fraction=target_fraction,
        target_notional_usd=target_notional,
        rebalance_delta_usd=delta,
        exposure_above_target_usd=max(0.0, -delta),
    )
    tolerance = portfolio.equity_usd * config.rebalance_tolerance_fraction
    # Accumulation tolerance must not prevent a valid, fixed-rung reduction.
    # All clock, evidence-context, loss and pending-order checks still precede it.
    if (momentum > 0 and abs(delta) < tolerance) or delta == 0:
        return _hold("within_rebalance_tolerance", metrics)
    # Positive momentum never forces a sale solely because a target moved.
    # Negative momentum never adds risk to an existing position.
    if delta < 0 and momentum > 0:
        return _hold("no_sell_signal", metrics)
    if delta > 0:
        amount = min(delta, config.order_notional_usd, portfolio.cash_usd)
        action: Literal["buy", "sell"] = "buy"
    else:
        amount = min(-delta, config.order_notional_usd, portfolio.btc_notional_usd)
        action = "sell"
    # Truncate to cents, never round an order beyond cash, holding, or limits.
    amount = math.floor(amount * 100) / 100
    if amount < config.min_order_notional_usd:
        return _hold(
            "residual_below_minimum_order_notional"
            if action == "sell"
            else "below_minimum_order_notional",
            metrics,
        )
    return Decision(
        action=action,
        notional_usd=amount,
        reasons=(
            "positive_momentum" if action == "buy" else "negative_momentum",
            "paper_proposal_requires_evidence_and_broker_authorization",
        ),
        metrics=metrics,
    )
