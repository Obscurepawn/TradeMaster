import random
import requests
from src.proxy.manager import ProxyManager

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
]

class RequestHook:
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager

    def get_headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS)
        }

    def before_request(self):
        """Called before making a request to rotate proxy."""
        self.proxy_manager.rotate_proxy()
        
    def get_session(self) -> requests.Session:
        """Returns a configured requests Session."""
        session = requests.Session()
        session.headers.update(self.get_headers())
        
        # Configure local Clash proxy (Standard port 7890)
        # In a robust system, this port would be configurable.
        session.proxies = {
            "http": "http://127.0.0.1:7890",
            "https": "http://127.0.0.1:7890"
        }
        
        # We hook into the session.request logic manually or just call hooks before usage.
        # For simplicity, we assume the caller calls before_request explicitly or we subclass Session.
        return session