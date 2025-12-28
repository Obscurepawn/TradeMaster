from typing import Dict
import pandas as pd
from src.contracts.interfaces import Strategy
from src.domain import Trade, TradeDirection

class PESmallCapStrategy(Strategy):
    def on_init(self, context):
        self.max_positions = 5  # 均仓：最多同时持有5只股票
        print(f"PE Small Cap Strategy Initialized (Equal Weight: {1/self.max_positions:.0%})")

    def on_bar(self, context, bar_dict: Dict[str, pd.Series]):
        # 1. 卖出逻辑 (保持不变)
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
                    print(f"[{bar.name}] SELL {code} @ {bar['close']:.2f} (PnL: {pnl_pct:.2%})")

        # 2. 买入逻辑 (均仓)
        num_current_positions = len(context.portfolio.positions)
        if num_current_positions >= self.max_positions:
            return

        # 计算每一份头寸的资金 (基于初始或当前总资产，这里使用当前总资产)
        target_pos_value = context.portfolio.total_value / self.max_positions

        for code, bar in bar_dict.items():
            if code not in context.portfolio.positions:
                # 检查剩余现金是否足够买入一份头寸
                if context.portfolio.cash >= target_pos_value and len(context.portfolio.positions) < self.max_positions:
                    quantity = int(target_pos_value / (bar['close'] * 100)) * 100
                    
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
                        print(f"[{bar.name}] BUY  {code} @ {bar['close']:.2f} (Equal Weight Qty: {quantity})")