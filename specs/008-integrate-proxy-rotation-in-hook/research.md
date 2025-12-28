# Research: Robust Clash Proxy Rotation

## Decision: Explicit `is_enabled` flag in `ProxyManager`
**Rationale**: Instead of just checking if the `proxies` list is empty, an explicit `is_enabled` flag (or similar) that is set during initialization after path validation makes the code more readable and intent-clear.
**Alternatives considered**:
- Checking path in every `rotate_proxy` call: Inefficient.
- Only relying on `proxies` list: Less informative for debugging why rotation is skipped.

## Decision: Error Logging Level
**Rationale**: The user specifically requested "error logs". We will use `logger.error` when the configuration is missing or invalid.
