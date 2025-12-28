# Research: Strengthen anti-anti-scraping mechanism

## Decision: Global Monkeypatching of `requests.Session.request`
**Rationale**: By patching `Session.request`, we intercept all high-level `requests.get/post` calls as well as direct session usage. This is the most non-intrusive way to force third-party libraries (like `akshare`) to use our rotated proxies and headers without modifying their source code.
**Alternatives considered**: 
- Setting environment variables (`HTTP_PROXY`): Simple but doesn't handle header rotation or jitter.
- Passing `proxies` and `headers` to every call: Too fragile and doesn't work for library calls.

## Decision: Realistic Browser Fingerprinting
**Rationale**: Modern WAFs (Web Application Firewalls) check for more than just `User-Agent`. Including `Sec-Fetch-Dest`, `Sec-Fetch-Mode`, `Sec-Fetch-Site`, and `Sec-CH-UA` (User-Agent Client Hints) significantly increases the difficulty of script detection.
**Implementation details**:
- `Sec-Fetch-Mode`: Usually `navigate` for main pages, `cors` or `no-cors` for data. We will default to `navigate` or `cors` based on common patterns.
- `Sec-CH-UA`: Must match the major version of the `User-Agent`.

## Decision: Proactive Proxy Health Checks
**Rationale**: Switching to a dead node in a rotating proxy pool causes request failure.
**Implementation details**:
- Use Clash External Controller API `GET /proxies/{name}/delay` to verify node connectivity before selection.
- Implement a "failed nodes" blacklist for the current session.

## Decision: Random Request Jitter
**Rationale**: Constant interval requests are a hallmark of bots.
**Implementation details**:
- Add a random delay between `0.5s` and `2.0s` (configurable) before each remote request.
- Use a non-linear jitter to further obscure patterns.

## Research Findings Summary
| Topic | Best Practice | Integration Plan |
|-------|---------------|------------------|
| Request Hook | Monkeypatch `requests.sessions.Session.request` | Implement in `src/proxy/hook.py` |
| Browser Headers | Use Client Hints and Fetch Metadata | Implement database in `src/proxy/headers.py` |
| Proxy Rotation | Health check via Clash API | Update `ProxyManager` in `src/proxy/manager.py` |
| Anti-Detection | Random jitter and Referral rotation | Add to `RequestHook` |
