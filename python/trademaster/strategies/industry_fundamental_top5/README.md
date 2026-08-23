# 行业基本面策略兼容入口

该目录保留旧模块路径兼容性。稳定公共入口已经改为：

```text
trademaster.strategies.industry_fundamental
```

`Top1`、`Top5`和“Top10行业×Top1”现在都是`IndustryFundamentalConfig`配置，不再作为包名或类型身份。

## 当前运行链路

九项基本面指标不再由策略私有函数计算。它们注册为9个managed atomic factors，并组合为：

- `fundamental.composite.industry_relative@1`：行业内选股；
- `fundamental.composite.global@1`：行业平均分排名。

每个真实调仓截面先发布完整candidate input snapshot，然后物化原子和复合因子；策略层只消费factor artifacts，执行行业筛选、TopN和精确等权。正式bundle v3同时绑定输入snapshot、11个definitions、220个materializations、selection、Rust结果及报告。

因子存储、血缘、评估与CLI详见[因子管理文档](../../../../docs/factor-management.md)。

## 真实十年运行

```bash
cargo build --release -p tm-runner
IFS= read -r trademaster_token < ~/.config/trademaster/tushare_token
TUSHARE_TOKEN="$trademaster_token" uv run python -m \
  trademaster.strategies.industry_fundamental \
  --data-root data/strategies/industry-fundamental-top5-10y \
  --output-root artifacts/strategies/industry-fundamental-managed-top1-final-v2 \
  --runner target/release/tm-runner \
  --industry-membership-policy historical_interval_required \
  --top-per-industry 1
```

相同参数再次运行时会先检查DuckDB，再复读真实Tushare Parquet和factor Parquet；无效token的clean replay必须保持provider调用为0。

## 数据边界

- 财务数据只使用公告日在信号前的`update_flag=0`原始版本；
- 正式模式按`index_member_all.in_date/out_date`解析信号日所属行业；任一候选缺历史区间即
  fail closed。`static_latest_experiment`只用于显式非PIT敏感性实验，并拥有不同策略definition；
- 股票价格继续使用`adj_factor`连续价格作为研究执行近似，不等同于公司行动现金账本；
- 执行日缺少精确状态/涨跌停证据会fail closed；ETF只有在显式绑定交易所limit-rule evidence时才能由规则计算界限。

2026-08-23的M9审查确认旧Top1/Top10报告曾把未来`in_date`的当前行业映射回填历史，且连续
复权价不能证明精确的100股/费用/公司行动会计。旧收益报告已降级为审查前诊断证据；在
historical interval coverage与Rust公司行动合同补齐前，不应引用为正式A股执行收益。
