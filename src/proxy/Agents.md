# Proxy Agent Context

## Responsibility
Provides infrastructure for bypassing rate limits and anti-scraping mechanisms. Logs proxy rotation events and API failures.

## Implementation
- `manager.py`: Communicates with a local Clash instance via its External Controller API to rotate proxy nodes.
- `hook.py`: Provides `requests.Session` configuration and headers management (including random User-Agents).
- Fully documented with Google Style docstrings.

## Testing
- `test_manager.py`: Verifies Clash configuration parsing.
- `test_hook.py`: Ensures session configuration and header generation logic.
