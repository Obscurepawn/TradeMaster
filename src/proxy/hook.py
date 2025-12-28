import time
import random
import logging
import requests
from typing import Optional
from src.proxy.manager import ProxyManager
from src.proxy.headers import get_random_fingerprint

logger = logging.getLogger(__name__)

# Global state to prevent redundant initialization
_HOOK_INITIALIZED = False
_ORIGINAL_REQUEST = requests.sessions.Session.request

# Configuration
JITTER_RANGE = (0.5, 2.0)
DEFAULT_PROXY_URL = "http://127.0.0.1:7890"


class GlobalRequestHook:
    """Implements global interception of the requests library."""

    def __init__(self, proxy_manager: Optional[ProxyManager] = None):
        self.proxy_manager = proxy_manager or ProxyManager()
        self.request_count = 0
        self.rotation_threshold = 1  # Rotate proxy every 5 requests

    def patched_request(self, session_instance: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
        """Custom replacement for requests.Session.request."""

        # 1. Skip hook for Clash API calls to avoid infinite recursion
        # Extract the base controller URL from proxy_manager
        controller_base = self.proxy_manager.api_url.replace(
            "http://", "").replace("https://", "")
        if controller_base in url:
            return _ORIGINAL_REQUEST(session_instance, method, url, **kwargs)

        # 2. Add Jitter
        delay = random.uniform(*JITTER_RANGE)
        logger.debug(f"Anti-Scraping: Jitter delay {delay:.2f}s before {url}")
        time.sleep(delay)

        # 3. Rotate Proxy periodically
        self.request_count += 1
        if self.request_count >= self.rotation_threshold:
            logger.info("Anti-Scraping: Threshold reached. Rotating proxy...")
            self.proxy_manager.rotate_proxy()
            self.request_count = 0

        # 4. Inject Headers (if not already explicitly provided for this specific call)
        # We merge global fingerprint headers with any user-provided headers
        fp = get_random_fingerprint()
        injected_headers = fp.to_headers()

        current_headers = kwargs.get("headers") or {}
        # User-provided headers take precedence
        merged_headers = injected_headers.copy()
        merged_headers.update(current_headers)
        kwargs["headers"] = merged_headers

        # 5. Enforce Proxy
        # Ensure we use the Clash local proxy port
        if "proxies" not in kwargs or kwargs["proxies"] is None:
            kwargs["proxies"] = {
                "http": DEFAULT_PROXY_URL,
                "https": DEFAULT_PROXY_URL
            }

        logger.debug(f"Anti-Scraping: Intercepted {method} {url}")

        # 6. Call original request method
        return _ORIGINAL_REQUEST(session_instance, method, url, **kwargs)


def init_anti_scraping(proxy_manager: Optional[ProxyManager] = None):
    """Initializes the global anti-scraping hook."""
    global _HOOK_INITIALIZED
    if _HOOK_INITIALIZED:
        logger.warning("Anti-Scraping hook already initialized.")
        return

    hook = GlobalRequestHook(proxy_manager)

    # Define a wrapper that passes 'self' (the session instance) correctly
    def hook_wrapper(self, method, url, **kwargs):
        return hook.patched_request(self, method, url, **kwargs)

    # Apply monkeypatch
    requests.sessions.Session.request = hook_wrapper
    _HOOK_INITIALIZED = True
    logger.info(
        "Global Anti-Scraping Hook Activated (Jitter + Header Rotation + Proxy Enforcement)")


def disable_anti_scraping():
    """Removes the global anti-scraping hook and restores original behavior."""
    global _HOOK_INITIALIZED
    if not _HOOK_INITIALIZED:
        return

    requests.sessions.Session.request = _ORIGINAL_REQUEST
    _HOOK_INITIALIZED = False
    logger.info("Global Anti-Scraping Hook Deactivated.")
