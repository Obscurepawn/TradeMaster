import random
import requests
from src.proxy.manager import ProxyManager

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
]

class RequestHook:

    """Provides utility methods for configuring network requests with headers and proxies.



    Attributes:

        proxy_manager: The ProxyManager instance used for rotation.

    """

    def __init__(self, proxy_manager: ProxyManager):

        """Initializes RequestHook.



        Args:

            proxy_manager: Instance for managing proxy state.

        """

        self.proxy_manager = proxy_manager



    def get_headers(self) -> dict:

        """Generates a random User-Agent header.



        Returns:

            A dictionary containing the User-Agent string.

        """

        return {

            "User-Agent": random.choice(USER_AGENTS)

        }



    def before_request(self):

        """Pre-request hook to trigger proxy rotation."""

        self.proxy_manager.rotate_proxy()

        

    def get_session(self) -> requests.Session:

        """Creates and configures a requests.Session with headers and proxies.



        Returns:

            A requests.Session object configured to use the local proxy.

        """

        session = requests.Session()

        session.headers.update(self.get_headers())

        

        # Configure local Clash proxy (Standard port 7890)

        session.proxies = {

            "http": "http://127.0.0.1:7890",

            "https": "http://127.0.0.1:7890"

        }

        

        return session
