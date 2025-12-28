import logging
import os
import yaml
import random
import requests
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class ProxyManager:
    """Manages proxy rotation by interacting with a local Clash instance API.

    This manager discovers proxy nodes directly from the Clash External Controller API.

    Attributes:
        api_url: URL for the Clash External Controller.
        api_secret: Authentication secret for the Clash API.
        config_path: Optional filesystem path to Clash config (Fallback).
        proxies: List of available proxy node names.
        blacklist: Set of proxy names that failed health checks recently.
        is_enabled: Whether the manager is successfully initialized.
    """

    def __init__(self,
                 api_url: Optional[str] = None,
                 api_secret: Optional[str] = None,
                 config_path: Optional[str] = None,
                 selector_name: str = "Proxy"):
        """Initializes ProxyManager. If config_path is provided, it extracts API info from it."""
        self.config_path = config_path
        self.api_url = api_url.rstrip('/') if api_url else "http://127.0.0.1:9090"
        self.api_secret = api_secret
        self.selector_name = selector_name

        # If config_path exists, try to extract external-controller and secret
        if self.config_path and os.path.exists(self.config_path):
            self._extract_api_info_from_config()

        self.proxies: List[str] = []
        self.blacklist: set[str] = set()
        self.is_enabled = False

        self.refresh_proxies()

    def _extract_api_info_from_config(self):
        """Parses the Clash YAML config to extract external-controller and secret."""
        try:
            with open(self.config_path, 'r') as f:
                data = yaml.safe_load(f)
                if not data:
                    return

                # Extract external-controller
                controller = data.get("external-controller")
                if controller:
                    # Handle cases where it's just "port" or "ip:port"
                    if ":" not in str(controller):
                        self.api_url = f"http://127.0.0.1:{controller}"
                    else:
                        self.api_url = f"http://{controller}"
                    logger.debug(
                        f"[ProxyManager] Extracted API URL from config: {self.api_url}")

                # Extract secret
                secret = data.get("secret")
                if secret:
                    self.api_secret = str(secret)
                    logger.debug(
                        "[ProxyManager] Extracted API secret from config.")

        except Exception as e:
            logger.error(
                f"[ProxyManager] Failed to extract API info from {self.config_path}: {e}")

    def _get_headers(self) -> Dict[str, str]:
        """Returns headers for Clash API requests including Authorization."""
        headers = {}
        if self.api_secret:
            headers["Authorization"] = f"Bearer {self.api_secret}"
        return headers

    def refresh_proxies(self):
        """Discovers available proxy nodes from the Clash API."""
        self.load_proxies_from_api()

    def load_proxies_from_api(self) -> bool:
        """Fetches all available proxy nodes from the Clash API."""
        try:
            url = f"{self.api_url}/proxies"
            # Crucial: proxies={} ensures this request never goes through a proxy
            resp = requests.get(url, headers=self._get_headers(
            ), timeout=3, proxies={"http": None, "https": None})
            if resp.status_code == 200:
                data = resp.json()
                all_proxies = data.get("proxies", {})

                node_names = []
                selector_groups = []
                for name, info in all_proxies.items():
                    if info.get("type") in ["Selector", "URLTest", "Fallback", "LoadBalance"]:
                        selector_groups.append(name)
                    else:
                        node_names.append(name)

                if node_names:
                    self.proxies = node_names
                    self.is_enabled = True
                    logger.info(f"Successfully discovered {len(self.proxies)} proxies via Clash API.")
                    logger.info(f"Available selectors (choose one for config): {selector_groups}")
                    return True
                else:
                    logger.error(f"[ProxyManager] API at {self.api_url} returned 200 but no valid proxy nodes found.")
            else:
                logger.error(f"[ProxyManager] API call failed. URL: {url}, Status: {resp.status_code}, Resp: {resp.text[:100]}")
        except Exception as e:
            logger.error(f"[ProxyManager] Failed to connect to Clash API at {self.api_url}. Error: {e}")
        return False

    def check_node_health(self, node_name: str, timeout: int = 3000) -> bool:
        """Checks the health of a specific proxy node via Clash API."""
        try:
            from urllib.parse import quote
            safe_name = quote(node_name, safe='')
            url = f"{self.api_url}/proxies/{safe_name}/delay"
            params = {
                "timeout": timeout,
                "url": "http://www.gstatic.com/generate_204"
            }
            resp = requests.get(url, params=params, headers=self._get_headers(),
                                timeout=timeout/1000 + 1, proxies={"http": None, "https": None})
            if resp.status_code == 200:
                return True
            return False
        except Exception:
            return False

    def rotate_proxy(self, selector_name: Optional[str] = None, max_retries: int = 5):
        """Selects a healthy proxy node and instructs Clash to switch to it.

        Args:
            selector_name: The name of the Clash proxy group selector. Defaults to instance value.
            max_retries: Maximum number of healthy nodes to try finding.
        """
        if selector_name is None:
            selector_name = self.selector_name

        if not self.is_enabled or not self.proxies:
            # Final attempt to refresh if we have nothing
            self.refresh_proxies()
            if not self.proxies:
                return

        available_nodes = [
            name for name in self.proxies if name not in self.blacklist]
        if not available_nodes:
            self.blacklist.clear()
            available_nodes = self.proxies

        for _ in range(max_retries):
            node_name = random.choice(available_nodes)

            if self.check_node_health(node_name):
                try:
                    from urllib.parse import quote
                    safe_selector = quote(selector_name, safe='')
                    url = f"{self.api_url}/proxies/{safe_selector}"
                    payload = {"name": node_name}

                    resp = requests.put(url, json=payload, headers=self._get_headers(),
                                        timeout=2, proxies={"http": None, "https": None})
                    if resp.status_code == 204:
                        logger.info(f"Successfully switched to proxy: {node_name}")
                        return
                    else:
                        logger.error(f"Failed to switch node via API. Status: {resp.status_code}, Selector: {selector_name}, Node: {node_name}, Response: {resp.text}")
                except Exception as e:
                    logger.debug(f"Clash API switch failed: {e}")
            else:
                logger.warning(f"Node {node_name} failed health check. Blacklisting.")
                self.blacklist.add(node_name)
                available_nodes = [n for n in available_nodes if n != node_name]
                if not available_nodes:
                    break
