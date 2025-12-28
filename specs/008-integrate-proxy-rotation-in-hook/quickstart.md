# Quickstart: Robust Proxy Rotation

## Troubleshooting Missing Proxies
If you see the following error in your logs:
`ERROR: [ProxyManager] Clash config path is missing or invalid. Proxy rotation is DISABLED.`

Check your `config.yaml`:
```yaml
proxy:
  clash_config_path: "/absolute/path/to/your/proxies.yaml"
```
Ensure the file exists and is readable. The system will continue to run but without rotating proxies.
