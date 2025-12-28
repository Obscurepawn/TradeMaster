# Proxy Agent Context

## Responsibility
Manages anti-scraping measures (IP rotation, User-Agent spoofing).

## Implementation
- `manager.py`: Interfaces with Clash API/Config to switch nodes.
- `hook.py`: `requests` middleware for headers.

## Testing
- `test_manager.py`: Mock Clash config parsing.