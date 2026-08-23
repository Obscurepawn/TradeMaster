# 行业基本面策略

这是可配置行业基本面策略的稳定公共入口。`Top1`、`Top5` 和“先选Top行业”都是配置，
不再体现在包名或类型名中。九项原子指标、行业内复合分和全市场复合分由
`trademaster.factors` 注册、物化和管理；策略只负责候选维度、行业选择、目标权重和调仓。

正式运行生成E2E bundle v3，绑定candidate input snapshot、11个factor definitions、每期11个
materializations、selection、Rust request/result、HTML、summary和Markdown。执行日缺少状态/涨跌停
证据时fail closed；同一target portfolio按卖出优先和instrument ID稳定执行，不受signal ID影响。

完整存储、血缘、评估和CLI说明见[因子管理文档](../../../../docs/factor-management.md)。

```bash
export TUSHARE_TOKEN="your-token"

uv run python -m \
  trademaster.strategies.industry_fundamental \
  --data-root data/strategies/industry-fundamental-top5-10y \
  --output-root artifacts/strategies/industry-fundamental-managed \
  --runner target/release/tm-runner \
  --top-per-industry 1
```

正式模式默认要求信号日存在历史行业`in_date/out_date`区间；Tushare源历史不足时会fail
closed，而不会用当前行业回填。已有完整本地cache时，相同请求会保持provider调用为0。

旧入口 `trademaster.strategies.industry_fundamental_top5` 暂时保留兼容性。
