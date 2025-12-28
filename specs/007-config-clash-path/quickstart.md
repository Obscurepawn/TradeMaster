# Quickstart: Configuring Clash Path in YAML

## New Configuration Format
Add a `proxy` section to your `config.yaml`:

```yaml
start_date: 2023-01-01
end_date: 2023-03-01
initial_cash: 100000.0
strategy_name: PESmallCap
# ... other fields ...

proxy:
  clash_config_path: "/path/to/your/clash/config.yaml"
  selector_name: "Proxy" # Optional, defaults to Proxy

logging:
  level: INFO
  # ...
```

## Environment Variable Deprecation
The `CLASH_CONFIG_PATH` environment variable is no longer used by the application when using the standard entry point. All configuration should be centralized in the YAML file.
