# Tasks: Strengthen anti-anti-scraping mechanism

## Phase 1: Header Database & Proxy Health (Infrastructure)
- [X] Create `src/proxy/headers.py` with `BrowserFingerprint` class and a collection of modern browser profiles (Chrome, Firefox, Safari).
- [X] Update `src/proxy/manager.py` to include node health checks via Clash API `/proxies/{name}/delay`.
- [X] Implement a `ProxyState` tracker in `ProxyManager` to manage node failures and blacklisting.
- [X] Add `test/proxy/test_headers.py` to verify header variety and consistency (UA matching Client Hints).

## Phase 2: Global Hook Implementation
- [X] Update `src/proxy/hook.py` to implement the `requests.Session.request` monkeypatch.
- [X] Implement `init_anti_scraping()` in `src/proxy/hook.py` as the entry point for activation.
- [X] Add random jitter logic using `time.sleep` and `random.uniform` within the hook.
- [X] Ensure the hook correctly handles merging of global headers/proxies with request-specific ones.
- [X] Add `test/proxy/test_hook.py` to verify that global requests are intercepted and modified.

## Phase 3: Main Path Integration & Verification
- [X] Modify `src/main.py` to call `init_anti_scraping()` at the beginning of the `main()` function.
- [X] Update `src/data_loader/akshare_loader.py` to remove redundant local proxy rotation if necessary (or ensure it complements the global hook).
- [X] Perform a real-world fetch test (e.g., fetching a small stock's daily data) to ensure no breakage.
- [X] Verify that `akshare` internal requests are indeed carrying the injected headers (can be done via a local httpbin or debugging proxy).

## Phase 4: Documentation & Cleanup
- [X] Update `src/proxy/Agents.md` to reflect the new global hook architecture.
- [X] Final verification of test coverage (> 70% for the new module).
