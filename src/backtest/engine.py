from datetime import date, timedelta
from typing import List, Dict
import pandas as pd
from src.contracts.interfaces import Strategy, DataSource
from src.backtest.portfolio import Portfolio
from src.domain import BacktestResult
from src.config.schema import BacktestConfig

class BacktestContext:
    def __init__(self, portfolio: Portfolio):
        self.portfolio = portfolio
        # Add API methods for strategy here (e.g., self.buy, self.sell)

class BacktestEngine:
    def __init__(self, config: BacktestConfig, data_source: DataSource, strategy: Strategy):
        self.config = config
        self.data_loader = data_source
        self.strategy = strategy
        self.portfolio = Portfolio(config.initial_cash)
        self.context = BacktestContext(self.portfolio)

    def run(self) -> BacktestResult:
        # 1. Initialize Strategy
        self.strategy.on_init(self.context)
        
        # 2. Fetch Data (Simplification: Fetch all upfront or lazy load)
        # For simplicity, we assume the Universe is defined or we fetch as we go.
        # But for iteration, we need a date range.
        current_date = self.config.start_date
        dates = []
        
        while current_date <= self.config.end_date:
            # 3. Check if trading day (Simplification: check if data exists)
            # In a real engine, we'd have a Calendar.
            # Here, we might query the Benchmark to see if it's a valid date.
            
            # Fetch daily bars for universe
            bar_dict = {}
            if self.config.universe:
                for code in self.config.universe:
                    df = self.data_loader.get_daily_bars(code, current_date, current_date)
                    if not df.empty:
                        bar_dict[code] = df.iloc[0] # Series
            
            if bar_dict: # If data exists for this day
                # Update portfolio prices
                for code, bar in bar_dict.items():
                    self.portfolio.update_price(code, bar['close'])
                
                # Run Strategy
                self.strategy.on_bar(self.context, bar_dict)
                
                # Record value
                self.portfolio.record_daily_value()
                dates.append(current_date)
            
            current_date += timedelta(days=1)
            
        # 4. Generate Result
        total_ret = (self.portfolio.total_value - self.config.initial_cash) / self.config.initial_cash
        
        return BacktestResult(
            total_return=total_ret,
            max_drawdown=0.0, # Placeholder
            sharpe_ratio=0.0, # Placeholder
            equity_curve=self.portfolio.history,
            dates=dates
        )