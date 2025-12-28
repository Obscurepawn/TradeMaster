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
        
        # 2. Pre-fetch all data for the universe
        all_data = {}
        if self.config.universe:
            for code in self.config.universe:
                df = self.data_loader.get_daily_bars(code, self.config.start_date, self.config.end_date)
                if not df.empty:
                    all_data[code] = df

        # 3. Execution Loop
        current_date = self.config.start_date
        dates = []
        
        while current_date <= self.config.end_date:
            # Check if this is a trading day for any stock in our universe
            bar_dict = {}
            for code, df in all_data.items():
                if current_date in df.index:
                    bar_dict[code] = df.loc[current_date]
            
            if bar_dict: 
                # Update portfolio prices
                for code, bar in bar_dict.items():
                    self.portfolio.update_price(code, bar['close'])
                
                # Run Strategy
                self.strategy.on_bar(self.context, bar_dict)
                
                # Record value
                self.portfolio.record_daily_value()
                dates.append(current_date)
            
            current_date += timedelta(days=1)
            
        # 4. Generate Baselines
        baselines_results = {}
        for b_code in self.config.baseline:
            b_df = self.data_loader.get_index_daily(b_code, self.config.start_date, self.config.end_date)
            if not b_df.empty:
                # Align with actual backtest dates (filter or reindex)
                b_series = b_df['close'].reindex(dates).ffill()
                # Normalize: divide by first available value
                first_val = b_series.dropna().iloc[0] if not b_series.dropna().empty else 1.0
                baselines_results[b_code] = (b_series / first_val).tolist()

        # 5. Generate Result
        total_ret = (self.portfolio.total_value - self.config.initial_cash) / self.config.initial_cash
        
        # Normalize strategy equity curve
        normalized_equity = [v / self.config.initial_cash for v in self.portfolio.history]

        return BacktestResult(
            total_return=total_ret,
            max_drawdown=0.0, # Placeholder
            sharpe_ratio=0.0, # Placeholder
            equity_curve=normalized_equity,
            baselines=baselines_results,
            dates=dates
        )