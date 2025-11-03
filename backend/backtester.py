from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import backtrader as bt
import pandas as pd
import tushare as ts

from .config import settings


class BacktestError(RuntimeError):
    """Raised when something goes wrong during backtesting."""


@dataclass
class StrategyRuntimeState:
    entry_price: Optional[float] = None
    highest_price: Optional[float] = None
    bars_since_entry: int = 0


def _to_tushare_date(date_str: str) -> str:
    return date_str.replace("-", "")


def _load_price_data(symbol: str, start_date: str, end_date: str) -> bt.feeds.PandasData:
    """Fetch daily price data from Tushare and convert it to a Backtrader data feed."""
    if not settings.tushare_token:
        raise BacktestError("Missing Tushare token. Define TUSHARE_TOKEN in your environment.")

    pro = ts.pro_api(settings.tushare_token)
    df = pro.daily(ts_code=symbol, start_date=_to_tushare_date(start_date), end_date=_to_tushare_date(end_date))
    if df.empty:
        raise BacktestError(f"No market data returned for symbol {symbol}.")

    df = df.sort_values("trade_date")
    df["datetime"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("datetime")
    df.rename(
        columns={
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "vol": "volume",
            "amount": "openinterest",
        },
        inplace=True,
    )
    return bt.feeds.PandasData(
        dataname=df[["open", "high", "low", "close", "volume", "openinterest"]],
        fromdate=dt.datetime.strptime(start_date, "%Y-%m-%d"),
        todate=dt.datetime.strptime(end_date, "%Y-%m-%d"),
    )


def _build_strategy_class(config: Dict[str, Any]) -> type[bt.Strategy]:
    entry_rules = config.get("entry", [])
    add_rules = config.get("add_position", [])
    reduce_rules = config.get("reduce_position", [])
    take_profit_rules = config.get("take_profit", [])
    risk_mgmt = config.get("risk_management", {})
    exit_rules = config.get("exit", [])
    portfolio = config.get("portfolio", {})

    class ConfiguredStrategy(bt.Strategy):
        params = dict(
            entry_rules=entry_rules,
            add_rules=add_rules,
            reduce_rules=reduce_rules,
            take_profit=take_profit_rules,
            risk=risk_mgmt,
            exit_rules=exit_rules,
            portfolio=portfolio,
        )

        def __init__(self) -> None:
            self.state = StrategyRuntimeState()
            self.order = None
            self.inds: Dict[str, Any] = {}
            self._init_indicators()

        def _init_indicators(self) -> None:
            for idx, rule in enumerate(self.p.entry_rules + self.p.add_rules + self.p.reduce_rules):
                rule_name = rule.get("rule", "").lower()
                params = rule.get("params", {}) or {}
                key_prefix = f"{rule_name}_{idx}"
                if rule_name == "sma_crossover":
                    fast_period = int(params.get("fast_period", 10))
                    slow_period = int(params.get("slow_period", 30))
                    fast = bt.indicators.SMA(self.datas[0].close, period=fast_period)
                    slow = bt.indicators.SMA(self.datas[0].close, period=slow_period)
                    self.inds[f"{key_prefix}_fast"] = fast
                    self.inds[f"{key_prefix}_slow"] = slow
                    self.inds[f"{key_prefix}_crossover"] = bt.indicators.CrossOver(fast, slow)
                elif rule_name == "ema_crossover":
                    fast_period = int(params.get("fast_period", 10))
                    slow_period = int(params.get("slow_period", 30))
                    fast = bt.indicators.EMA(self.datas[0].close, period=fast_period)
                    slow = bt.indicators.EMA(self.datas[0].close, period=slow_period)
                    self.inds[f"{key_prefix}_fast"] = fast
                    self.inds[f"{key_prefix}_slow"] = slow
                    self.inds[f"{key_prefix}_crossover"] = bt.indicators.CrossOver(fast, slow)
                elif rule_name == "rsi_oversold":
                    period = int(params.get("rsi_period", 14))
                    self.inds[f"{key_prefix}_rsi"] = bt.indicators.RSI(self.datas[0], period=period)
                elif rule_name == "price_breakout":
                    period = int(params.get("breakout_period", 20))
                    self.inds[f"{key_prefix}_highest"] = bt.indicators.Highest(self.datas[0].high, period=period)
                # ATR for stop loss if required
            stop_loss = self.p.risk.get("stop_loss", {})
            if stop_loss.get("rule") == "atr_stop":
                atr_period = int(stop_loss.get("params", {}).get("atr_period", 14))
                self.inds["atr"] = bt.indicators.ATR(self.datas[0], period=atr_period)

        def notify_order(self, order: bt.Order) -> None:
            if order.status in [order.Completed]:
                if order.isbuy():
                    self.state.entry_price = order.executed.price
                    self.state.highest_price = order.executed.price
                    self.state.bars_since_entry = 0
                elif order.issell() and self.position.size == 0:
                    self.state = StrategyRuntimeState()
            if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
                self.order = None

        def next(self) -> None:
            if self.order:
                return

            if self.position:
                self.state.bars_since_entry += 1
                if self.state.highest_price is not None:
                    self.state.highest_price = max(self.state.highest_price, self.data.close[0])

                self._maybe_add_position()
                self._maybe_reduce_position()
                if self._check_exit_conditions():
                    return
            else:
                if self._check_entry_conditions():
                    return

        # --- helpers -----------------------------------------------------

        def _calc_size(self, order_pct: float) -> int:
            cash = self.broker.get_cash()
            if order_pct <= 0:
                return 0
            alloc = cash * min(order_pct, 1.0)
            price = self.datas[0].close[0]
            size = int(alloc / price)
            return max(size, 0)

        def _check_entry_conditions(self) -> bool:
            for idx, rule in enumerate(self.p.entry_rules):
                rule_name = rule.get("rule", "").lower()
                params = rule.get("params", {}) or {}
                order_pct = float(params.get("order_pct", self.p.portfolio.get("default_order_pct", 0.95)))
                if self._evaluate_rule(rule_name, params, idx):
                    risk_per_trade = float(self.p.risk.get("risk_per_trade", 1.0))
                    order_pct = min(order_pct, risk_per_trade)
                    size = self._calc_size(order_pct)
                    if size > 0:
                        self.order = self.buy(size=size)
                        return True
            return False

        def _maybe_add_position(self) -> None:
            for idx, rule in enumerate(self.p.add_rules):
                rule_name = rule.get("rule", "").lower()
                params = rule.get("params", {}) or {}
                if self._evaluate_rule(rule_name, params, idx):
                    order_pct = float(params.get("order_pct", 0.2))
                    size = self._calc_size(order_pct)
                    if size > 0:
                        self.order = self.buy(size=size)
                        return

        def _maybe_reduce_position(self) -> None:
            for idx, rule in enumerate(self.p.reduce_rules):
                rule_name = rule.get("rule", "").lower()
                params = rule.get("params", {}) or {}
                if self._evaluate_rule(rule_name, params, idx):
                    sell_fraction = float(params.get("order_pct", 0.5))
                    size = int(self.position.size * sell_fraction)
                    if size > 0:
                        self.order = self.sell(size=size)
                        return

        def _check_exit_conditions(self) -> bool:
            price = self.data.close[0]
            if self._check_take_profit(price):
                return True
            if self._check_stop_loss(price):
                return True
            if self._check_exit_rules(price):
                return True
            return False

        def _check_take_profit(self, price: float) -> bool:
            for rule in self.p.take_profit:
                name = rule.get("rule", "").lower()
                params = rule.get("params", {}) or {}
                if name == "percent_target":
                    pct = float(params.get("percent", 0.05))
                    if self.state.entry_price and price >= self.state.entry_price * (1 + pct):
                        self.order = self.sell(size=self.position.size)
                        return True
                elif name == "trailing_stop":
                    pct = float(params.get("percent", 0.05))
                    if self.state.highest_price and price <= self.state.highest_price * (1 - pct):
                        self.order = self.sell(size=self.position.size)
                        return True
            return False

        def _check_stop_loss(self, price: float) -> bool:
            stop_loss = self.p.risk.get("stop_loss", {}) or {}
            rule = stop_loss.get("rule", "").lower()
            params = stop_loss.get("params", {}) or {}
            if rule == "percent_floor":
                pct = float(params.get("percent", 0.02))
                if self.state.entry_price and price <= self.state.entry_price * (1 - pct):
                    self.order = self.sell(size=self.position.size)
                    return True
            elif rule == "atr_stop":
                multiplier = float(params.get("atr_multiplier", 3.0))
                atr = self.inds.get("atr")
                if atr is not None and self.state.entry_price:
                    stop_price = self.state.entry_price - multiplier * atr[0]
                    if price <= stop_price:
                        self.order = self.sell(size=self.position.size)
                        return True
            return False

        def _check_exit_rules(self, price: float) -> bool:
            for rule in self.p.exit_rules:
                name = rule.get("rule", "").lower()
                params = rule.get("params", {}) or {}
                if name == "time_stop":
                    days = int(params.get("days", 10))
                    if self.state.bars_since_entry >= days:
                        self.order = self.sell(size=self.position.size)
                        return True
                elif name == "trailing_stop":
                    pct = float(params.get("percent", 0.05))
                    if self.state.highest_price and price <= self.state.highest_price * (1 - pct):
                        self.order = self.sell(size=self.position.size)
                        return True
            return False

        def _evaluate_rule(self, name: str, params: Dict[str, Any], idx: int) -> bool:
            key_prefix = f"{name}_{idx}"
            if name == "sma_crossover" or name == "ema_crossover":
                crossover = self.inds.get(f"{key_prefix}_crossover")
                return bool(crossover and crossover[0] > 0)
            if name == "rsi_oversold":
                rsi = self.inds.get(f"{key_prefix}_rsi")
                threshold = float(params.get("rsi_threshold", 30))
                return bool(rsi and rsi[0] < threshold)
            if name == "price_breakout":
                highest = self.inds.get(f"{key_prefix}_highest")
                return bool(highest and self.data.close[0] > highest[-1])
            return False

    return ConfiguredStrategy


def run_backtest(
    config: Dict[str, Any],
    symbol: str,
    start_date: str,
    end_date: str,
    initial_cash: float = 100_000.0,
) -> Dict[str, Any]:
    """Run a single backtest using the provided strategy configuration."""
    data_feed = _load_price_data(symbol, start_date, end_date)
    strategy_cls = _build_strategy_class(config)

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    commission = float(config.get("portfolio", {}).get("commission", 0.001))
    cerebro.broker.setcommission(commission=commission)
    cerebro.adddata(data_feed)
    cerebro.addstrategy(strategy_cls)
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", timeframe=bt.TimeFrame.Days, annualize=True)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    results = cerebro.run()
    strategy: bt.Strategy = results[0]

    final_value = cerebro.broker.getvalue()
    pnl = final_value - initial_cash
    roi = pnl / initial_cash
    sharpe = strategy.analyzers.sharpe.get_analysis().get("sharperatio", None)
    drawdown = strategy.analyzers.drawdown.get_analysis()
    trade_analysis = strategy.analyzers.trades.get_analysis()

    metrics = {
        "initial_cash": initial_cash,
        "final_value": final_value,
        "pnl": pnl,
        "roi": roi,
        "sharpe": sharpe,
        "drawdown": {
            "max": drawdown.get("max", {}).get("drawdown"),
            "max_len": drawdown.get("max", {}).get("len"),
        },
        "trades": {
            "total": trade_analysis.get("total", {}).get("total"),
            "won": trade_analysis.get("won", {}).get("total"),
            "lost": trade_analysis.get("lost", {}).get("total"),
        },
    }

    summary_lines = [
        f"Initial capital: {initial_cash:,.2f}",
        f"Final value: {final_value:,.2f}",
        f"Net PnL: {pnl:,.2f}",
        f"ROI: {roi:.2%}",
        f"Sharpe ratio: {sharpe:.2f}" if sharpe is not None else "Sharpe ratio: n/a",
        f"Max drawdown: {metrics['drawdown']['max']:.2f}%",
        f"Trades (win/loss/total): {metrics['trades']['won']}/{metrics['trades']['lost']}/{metrics['trades']['total']}",
    ]

    return {
        "summary": "\n".join(summary_lines),
        "metrics": metrics,
    }
