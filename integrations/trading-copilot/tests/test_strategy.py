"""Synthetic boundary fixtures, not historical or profitability evidence."""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from unittest import TestCase

from liquilens_trading_copilot.strategy import (
    MarketBar,
    PortfolioSnapshot,
    StrategyConfig,
    propose,
)

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
CONFIG = StrategyConfig()
PORTFOLIO = PortfolioSnapshot(100_000, 100_000, 0, 0, 0)


def sample_bars(
    *, trend: float = 0.01, count: int = 30, noise: float = 0.001
) -> list[MarketBar]:
    """Generate complete hourly closes; no actual market data is represented."""
    return [
        MarketBar(
            at=NOW - timedelta(hours=count - 1 - index),
            close=30_000 * math.exp(trend * index + noise * math.sin(index)),
        )
        for index in range(count)
    ]


class StrategyTests(TestCase):
    def decision(self, **kwargs: object):  # type: ignore[no-untyped-def]
        values: dict[str, object] = {
            "bars": sample_bars(),
            "portfolio": PORTFOLIO,
            "config": CONFIG,
            "now": NOW,
            "seiche_regime": "CALM",
        }
        values.update(kwargs)
        return propose(**values)  # type: ignore[arg-type]

    def test_default_positive_momentum_is_capped_paper_proposal(self) -> None:
        decision = self.decision()
        self.assertEqual(decision.action, "buy")
        self.assertEqual(decision.notional_usd, 1000)
        self.assertIn(
            "paper_proposal_requires_evidence_and_broker_authorization",
            decision.reasons,
        )
        self.assertEqual(decision.metrics["target_exposure_fraction"], 0.10)

    def test_public_dataclasses_are_frozen(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            CONFIG.fast_window = 6  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            PORTFOLIO.cash_usd = 1  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            sample_bars()[0].close = 1  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            self.decision().action = "buy"

    def test_negative_signal_reduces_existing_position_without_shorting(self) -> None:
        portfolio = replace(PORTFOLIO, cash_usd=98_500, btc_notional_usd=1500)
        decision = self.decision(bars=sample_bars(trend=-0.01), portfolio=portfolio)
        self.assertEqual(decision.action, "sell")
        self.assertEqual(decision.notional_usd, 1000)
        self.assertLessEqual(decision.notional_usd, portfolio.btc_notional_usd)

    def test_negative_signal_without_holding_does_not_short(self) -> None:
        decision = self.decision(bars=sample_bars(trend=-0.01))
        self.assertEqual(decision.action, "hold")
        self.assertIsNone(decision.notional_usd)

    def test_exact_minimum_holding_can_exit_on_negative_momentum(self) -> None:
        portfolio = replace(PORTFOLIO, cash_usd=99_000, btc_notional_usd=1000)
        decision = self.decision(bars=sample_bars(trend=-0.01), portfolio=portfolio)
        self.assertEqual(decision.action, "sell")
        self.assertEqual(decision.notional_usd, 1000)

    def test_small_holding_is_not_rounded_up_or_oversold(self) -> None:
        portfolio = replace(PORTFOLIO, cash_usd=99_000.01, btc_notional_usd=999.99)
        decision = self.decision(
            bars=sample_bars(trend=-0.01),
            portfolio=portfolio,
            config=replace(CONFIG, rebalance_tolerance_fraction=0),
        )
        self.assertEqual(decision.reasons, ("residual_below_minimum_order_notional",))

    def test_no_churn_inside_neutral_momentum_band(self) -> None:
        decision = self.decision(bars=sample_bars(trend=0, noise=0.00001))
        self.assertEqual(decision.reasons, ("momentum_in_neutral_band",))

    def test_no_churn_near_existing_target(self) -> None:
        portfolio = replace(PORTFOLIO, cash_usd=90_500, btc_notional_usd=9500)
        decision = self.decision(portfolio=portfolio)
        self.assertEqual(decision.reasons, ("within_rebalance_tolerance",))

    def test_positive_signal_does_not_force_sale_when_over_target(self) -> None:
        portfolio = replace(PORTFOLIO, cash_usd=80_000, btc_notional_usd=20_000)
        decision = self.decision(portfolio=portfolio)
        self.assertEqual(decision.reasons, ("no_sell_signal",))

    def test_buy_never_exceeds_cash_or_target_remainder(self) -> None:
        config = replace(
            CONFIG, min_order_notional_usd=1, rebalance_tolerance_fraction=0
        )
        portfolio = replace(PORTFOLIO, cash_usd=12.345)
        decision = self.decision(portfolio=portfolio, config=config)
        self.assertEqual(decision.notional_usd, 12.34)
        portfolio = replace(PORTFOLIO, cash_usd=90_012.34, btc_notional_usd=9987.66)
        decision = self.decision(portfolio=portfolio, config=config)
        self.assertLessEqual(decision.notional_usd, 12.34)
        self.assertLessEqual(portfolio.btc_notional_usd + decision.notional_usd, 10_000)

    def test_default_does_not_buy_with_less_than_published_size(self) -> None:
        decision = self.decision(portfolio=replace(PORTFOLIO, cash_usd=999.99))
        self.assertEqual(decision.reasons, ("below_minimum_order_notional",))

    def test_volatility_sizing_reduces_exposure(self) -> None:
        decision = self.decision(
            bars=sample_bars(noise=0.1),
            config=replace(CONFIG, momentum_threshold=0.0001),
        )
        self.assertEqual(decision.action, "buy")
        self.assertGreater(decision.metrics["annualized_volatility"], 2)
        self.assertLess(decision.metrics["target_exposure_fraction"], 0.10)

    def test_erosion_halves_position_target(self) -> None:
        calm = self.decision()
        erosion = self.decision(seiche_regime="EROSION")
        self.assertEqual(
            erosion.metrics["target_notional_usd"],
            calm.metrics["target_notional_usd"] / 2,
        )

    def test_strain_and_stress_block_new_exposure(self) -> None:
        for regime in ("STRAIN", "STRESS"):
            with self.subTest(regime=regime):
                decision = self.decision(seiche_regime=regime)
                self.assertEqual(
                    decision.reasons, ("seiche_regime_blocks_new_exposure",)
                )

    def test_stress_can_reduce_existing_holding_on_negative_momentum(self) -> None:
        decision = self.decision(
            bars=sample_bars(trend=-0.01),
            portfolio=replace(PORTFOLIO, cash_usd=98_500, btc_notional_usd=1500),
            seiche_regime="STRESS",
        )
        self.assertEqual(decision.action, "sell")

    def test_missing_or_unknown_regime_holds(self) -> None:
        for regime in (None, "UNKNOWN", "calm", "", []):
            with self.subTest(regime=regime):
                self.assertEqual(
                    self.decision(seiche_regime=regime).reasons,
                    ("seiche_regime_unavailable",),
                )

    def test_daily_loss_halts_both_sides_at_boundary_without_liquidation(self) -> None:
        # Start-of-period equity was $100,000; a $2,000 loss is exactly 2%.
        portfolio = PortfolioSnapshot(96_000, 98_000, 2000, -2000, 0)
        for trend in (-0.01, 0.01):
            with self.subTest(trend=trend):
                decision = self.decision(
                    portfolio=portfolio, bars=sample_bars(trend=trend)
                )
                self.assertEqual(decision.reasons, ("daily_loss_stop",))
                self.assertEqual(decision.action, "hold")
                self.assertIsNone(decision.notional_usd)

    def test_pending_order_holds_even_if_sell_signal(self) -> None:
        decision = self.decision(
            bars=sample_bars(trend=-0.01),
            portfolio=replace(PORTFOLIO, open_orders=1),
        )
        self.assertEqual(decision.reasons, ("open_orders_pending",))

    def test_incomplete_lookback_holds(self) -> None:
        for count in (0, 1, 29):
            with self.subTest(count=count):
                self.assertEqual(
                    self.decision(bars=sample_bars(count=count)).reasons,
                    ("insufficient_bars",),
                )
        decision = self.decision(config=replace(CONFIG, slow_window=60))
        self.assertEqual(decision.reasons, ("insufficient_bars",))

    def test_oversized_history_holds(self) -> None:
        decision = self.decision(bars=sample_bars(count=10_001))
        self.assertEqual(decision.reasons, ("too_many_bars",))

    def test_future_close_holds(self) -> None:
        bars = sample_bars()
        bars[-1] = replace(bars[-1], at=NOW + timedelta(microseconds=1))
        self.assertEqual(self.decision(bars=bars).reasons, ("future_bar",))

    def test_stale_close_and_exact_age_boundary(self) -> None:
        boundary = self.decision(now=NOW + timedelta(seconds=7200))
        self.assertEqual(boundary.action, "buy")
        stale = self.decision(now=NOW + timedelta(seconds=7200, microseconds=1))
        self.assertEqual(stale.reasons, ("stale_bars",))

    def test_duplicate_and_reversed_bars_hold(self) -> None:
        bars = sample_bars()
        bars[15] = replace(bars[15], at=bars[14].at)
        self.assertEqual(self.decision(bars=bars).reasons, ("non_monotonic_bars",))
        self.assertEqual(
            self.decision(bars=list(reversed(sample_bars()))).reasons,
            ("non_monotonic_bars",),
        )

    def test_gap_or_subinterval_does_not_pass_as_complete_history(self) -> None:
        for shift in (-1, 1):
            with self.subTest(shift=shift):
                bars = sample_bars()
                bars[10] = replace(bars[10], at=bars[10].at + timedelta(seconds=shift))
                self.assertEqual(
                    self.decision(bars=bars).reasons, ("incomplete_bar_sequence",)
                )

    def test_naive_clock_and_bar_hold(self) -> None:
        self.assertEqual(
            self.decision(now=NOW.replace(tzinfo=None)).reasons, ("invalid_clock",)
        )
        bars = sample_bars()
        bars[0] = replace(bars[0], at=bars[0].at.replace(tzinfo=None))
        self.assertEqual(self.decision(bars=bars).reasons, ("invalid_bar",))

    def test_nonfinite_nonpositive_and_malformed_close_hold(self) -> None:
        for close in (0, -1, math.inf, -math.inf, math.nan, True, "120", 10**1000):
            with self.subTest(close=close):
                bars = sample_bars()
                bars[0] = replace(bars[0], close=close)  # type: ignore[arg-type]
                self.assertEqual(self.decision(bars=bars).reasons, ("invalid_bar",))

    def test_wrong_bar_type_and_non_sequence_hold(self) -> None:
        bars = sample_bars()
        bars[0] = {"at": NOW, "close": 12}  # type: ignore[assignment]
        self.assertEqual(self.decision(bars=bars).reasons, ("invalid_bar",))
        for bars_value in (None, "bars", iter(sample_bars())):
            with self.subTest(bars=bars_value):
                self.assertEqual(
                    self.decision(bars=bars_value).reasons, ("invalid_bars",)
                )

    def test_zero_volatility_does_not_create_unbounded_position(self) -> None:
        for trend in (0, 0.01):
            with self.subTest(trend=trend):
                decision = self.decision(bars=sample_bars(trend=trend, noise=0))
                self.assertEqual(decision.reasons, ("volatility_unavailable",))

    def test_extreme_finite_prices_do_not_overflow(self) -> None:
        bars = [
            replace(bar, close=1e308 if index % 2 else 1e-308)
            for index, bar in enumerate(sample_bars())
        ]
        decision = self.decision(bars=bars)
        self.assertEqual(decision.action, "hold")
        for value in decision.metrics.values():
            if isinstance(value, float):
                self.assertTrue(math.isfinite(value))

    def test_invalid_portfolio_never_proposes(self) -> None:
        invalid = (
            replace(PORTFOLIO, cash_usd=-1),
            replace(PORTFOLIO, cash_usd=math.inf),
            replace(PORTFOLIO, cash_usd=True),
            replace(PORTFOLIO, cash_usd=10**1000),
            replace(PORTFOLIO, equity_usd=0),
            PortfolioSnapshot(1.7e308, 1.7e308, 1.7e308, 0, 0),
            replace(PORTFOLIO, btc_notional_usd=-1),
            replace(PORTFOLIO, btc_notional_usd=1),  # Inconsistent cash + holding.
            replace(PORTFOLIO, btc_notional_usd=200_000),
            replace(PORTFOLIO, daily_pnl_usd=math.nan),
            replace(PORTFOLIO, open_orders=-1),
            replace(PORTFOLIO, open_orders=True),
        )
        for portfolio in invalid:
            with self.subTest(portfolio=portfolio):
                self.assertEqual(
                    self.decision(portfolio=portfolio).reasons,
                    ("invalid_portfolio",),
                )

    def test_invalid_starting_equity_holds(self) -> None:
        decision = self.decision(portfolio=replace(PORTFOLIO, daily_pnl_usd=100_000))
        self.assertEqual(decision.reasons, ("invalid_daily_pnl_basis",))

    def test_invalid_or_unbounded_config_holds(self) -> None:
        invalid = (
            replace(CONFIG, fast_window=True),
            replace(CONFIG, fast_window=30),
            replace(CONFIG, slow_window=721),
            replace(CONFIG, min_bars=29),
            replace(CONFIG, bar_interval_seconds=0),
            replace(CONFIG, max_stale_seconds=999_999),
            replace(CONFIG, momentum_threshold=0),
            replace(CONFIG, target_annualized_volatility=math.nan),
            replace(CONFIG, max_portfolio_exposure=1),
            replace(CONFIG, max_daily_loss_fraction=1),
            replace(CONFIG, order_notional_usd=10_001),
            replace(CONFIG, min_order_notional_usd=1001),
            replace(CONFIG, rebalance_tolerance_fraction=0.10),
            replace(CONFIG, max_open_orders=1),
        )
        for config in invalid:
            with self.subTest(config=config):
                self.assertEqual(
                    self.decision(config=config).reasons, ("invalid_config",)
                )
