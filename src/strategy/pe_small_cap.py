import logging
from typing import Dict
import pandas as pd
from src.contracts.interfaces import Strategy
from src.domain import Trade, TradeDirection

logger = logging.getLogger(__name__)


class PESmallCapStrategy(Strategy):
    """Strategy that picks small cap stocks with low Price-to-Earnings ratios.

    This implementation uses an equal-weight approach, holding a fixed maximum
    number of positions.

    Attributes:
        max_positions: Maximum number of stocks to hold simultaneously.
    """

    def on_init(self, context):
        """Initializes strategy parameters and logging.

        Args:
            context: The backtest execution context.
        """
        self.max_positions = 5  # Equal weight: max 5 positions
        logger.info(f"PE Small Cap Strategy Initialized (Equal Weight: {1/self.max_positions:.0%})")

    def on_bar(self, context, bar_dict: Dict[str, pd.Series]):
        """Executes daily trading logic: selling exits and buying new entries.

        Args:
            context: Access to portfolio state and execution API.
            bar_dict: Market data bars for the current day.
        """
        # 1. Sell Logic
        for code, pos in list(context.portfolio.positions.items()):
            if code in bar_dict:
                bar = bar_dict[code]
                pnl_pct = (bar['close'] - pos.avg_cost) / pos.avg_cost
                if pnl_pct > 0.05 or pnl_pct < -0.02:
                    t = Trade(
                        date=bar.name,
                        code=code,
                        direction=TradeDirection.SELL,
                        quantity=pos.quantity,
                        price=bar['close'],
                        cost=0,
                        commission=0
                    )
                    context.portfolio.execute_trade(t)
                    logger.info(f"[{bar.name}] SELL {code} @ {bar['close']:.2f} (PnL: {pnl_pct:.2%})")

        # 2. Buy Logic (Equal Weight)
        num_current_positions = len(context.portfolio.positions)
        if num_current_positions >= self.max_positions:
            return

        # Calculate target position value based on current total assets
        target_pos_value = context.portfolio.total_value / self.max_positions

        for code, bar in bar_dict.items():
            if code not in context.portfolio.positions:
                # Check if enough cash is available for one target position
                if context.portfolio.cash >= target_pos_value and len(context.portfolio.positions) < self.max_positions:
                    quantity = int(target_pos_value /
                                   (bar['close'] * 100)) * 100

                    if quantity > 0:
                        cost = bar['close'] * quantity
                        t = Trade(
                            date=bar.name,
                            code=code,
                            direction=TradeDirection.BUY,
                            quantity=quantity,
                            price=bar['close'],
                            cost=cost,
                            commission=0
                        )
                        context.portfolio.execute_trade(t)
                        logger.info(f"[{bar.name}] BUY  {code} @ {bar['close']:.2f} (Equal Weight Qty: {quantity})")
