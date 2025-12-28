from typing import Dict
import pandas as pd
from src.contracts.interfaces import Strategy
from src.domain import Trade, TradeDirection

class PESmallCapStrategy(Strategy):
    def on_init(self, context):
        self.target_position_size = 1000  # shares
        self.holding_period = 10
        self.bars_held = 0
        print("PE Small Cap Strategy Initialized")

    def on_bar(self, context, bar_dict: Dict[str, pd.Series]):
        # Very simple dummy logic:
        # Buy if not held, Sell after N days
        
        # Check current positions
        # context.portfolio.positions is Dict[str, Position]
        
        # Iterate over universe available today
        for code, bar in bar_dict.items():
            current_pos = context.portfolio.positions.get(code)
            
            if not current_pos:
                # Buy Logic (Dummy: Buy everything)
                # Real logic: Check PE < X and MarketCap < Y
                # Requires fetching fundamental data, which isn't in daily bars.
                # For this framework demo, we assume we just buy.
                
                # Check cash
                cost = bar['close'] * self.target_position_size
                if context.portfolio.cash >= cost:
                    t = Trade(
                        date=bar.name, # Index is date
                        code=code,
                        direction=TradeDirection.BUY,
                        quantity=self.target_position_size,
                        price=bar['close'],
                        cost=cost,
                        commission=0 # Simplify
                    )
                    context.portfolio.execute_trade(t)
                    print(f"[{bar.name}] BUY  {code} @ {bar['close']:.2f}")
            
            else:
                # Sell Logic
                # Sell if price rose 5% or dropped 2% (Stop Loss / Take Profit)
                pnl_pct = (bar['close'] - current_pos.avg_cost) / current_pos.avg_cost
                
                if pnl_pct > 0.05 or pnl_pct < -0.02:
                     t = Trade(
                        date=bar.name,
                        code=code,
                        direction=TradeDirection.SELL,
                        quantity=current_pos.quantity,
                        price=bar['close'],
                        cost=0,
                        commission=0
                    )
                     context.portfolio.execute_trade(t)
                     print(f"[{bar.name}] SELL {code} @ {bar['close']:.2f} (PnL: {pnl_pct:.2%})")