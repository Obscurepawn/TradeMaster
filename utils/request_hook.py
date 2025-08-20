import requests
import random
from typing import Any, Dict, Optional
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
from logger.logger import get_logger

logger = get_logger(__name__)

# User-Agent list for anti-anti-crawling
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 Edg/91.0.864.59",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
]

# Store the original requests methods
_original_get = requests.get
_original_post = requests.post
_original_request = requests.Session.request

# Global variable to store the current User-Agent
_current_user_agent = None


def get_random_user_agent() -> str:
    """Get a random User-Agent from the list"""
    return random.choice(USER_AGENTS)


def set_current_user_agent(user_agent: str) -> None:
    """Set the current User-Agent to be used in requests"""
    global _current_user_agent
    _current_user_agent = user_agent


def get_current_user_agent() -> Optional[str]:
    """Get the current User-Agent"""
    global _current_user_agent
    return _current_user_agent


def update_headers_with_user_agent(
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Update headers with User-Agent"""
    if headers is None:
        headers = {}

    # Use the current User-Agent if set, otherwise get a random one
    user_agent = get_current_user_agent() or get_random_user_agent()
    headers["User-Agent"] = user_agent
    return headers


def hooked_get_with_user_agent(url: str, **kwargs: Any) -> requests.Response:
    """Hooked version of requests.get"""
    # Update headers with User-Agent
    kwargs["headers"] = update_headers_with_user_agent(kwargs.get("headers"))
    logger.info(
        f"Making GET request to {url} with User-Agent: {kwargs['headers']['User-Agent']}"
    )
    return _original_get(url, **kwargs)


def hooked_post_with_user_agent(url: str, **kwargs: Any) -> requests.Response:
    """Hooked version of requests.post"""
    # Update headers with User-Agent
    kwargs["headers"] = update_headers_with_user_agent(kwargs.get("headers"))
    logger.info(
        f"Making POST request to {url} with User-Agent: {kwargs['headers']['User-Agent']}"
    )
    return _original_post(url, **kwargs)


def hooked_request_with_user_agent(
    self: requests.Session, method: str, url: str, **kwargs: Any
) -> requests.Response:
    """Hooked version of requests.Session.request"""
    # Update headers with User-Agent
    kwargs["headers"] = update_headers_with_user_agent(kwargs.get("headers"))
    logger.info(
        f"Making {method} request to {url} with User-Agent: {kwargs['headers']['User-Agent']}"
    )
    return _original_request(self, method, url, **kwargs)


def install_user_agent_hooks() -> None:
    """Install the HTTP request hooks"""
    global _original_get, _original_post, _original_request

    # Replace the original methods with hooked versions
    requests.get = hooked_get_with_user_agent
    requests.post = hooked_post_with_user_agent
    requests.Session.request = hooked_request_with_user_agent

    logger.info("HTTP request hooks installed successfully")


def uninstall_hooks() -> None:
    """Uninstall the HTTP request hooks and restore original methods"""
    global _original_get, _original_post, _original_request

    # Restore original methods
    requests.get = _original_get
    requests.post = _original_post
    requests.Session.request = _original_request

    logger.info("HTTP request hooks uninstalled successfully")


class RequestHookUserAgentContext:
    """Context manager for temporarily setting a User-Agent"""

    def __init__(self, user_agent: Optional[str] = None):
        self.user_agent = user_agent or get_random_user_agent()
        self.previous_user_agent = None

    def __enter__(self):
        global _current_user_agent
        self.previous_user_agent = _current_user_agent
        set_current_user_agent(self.user_agent)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _current_user_agent
        _current_user_agent = self.previous_user_agent
