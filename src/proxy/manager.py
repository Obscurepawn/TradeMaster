import os
import yaml
import random
import requests
from typing import List, Dict, Optional

class ProxyManager:
    """Manages proxy rotation by interacting with a local Clash instance.

    This manager loads proxy node configurations from a YAML file and
    communicates with the Clash External Controller API to rotate proxies.

    Attributes:
        config_path: Path to the Clash config file.
        controller_url: URL for the Clash External Controller.
        proxies: List of available proxy nodes.
    """
    def __init__(self, config_path: Optional[str] = None, controller_url: str = "http://127.0.0.1:9090"):
        """Initializes ProxyManager and loads configurations.

        Args:
            config_path: Optional filesystem path to Clash config.
            controller_url: Base URL for Clash API.
        """
        self.config_path = config_path or os.environ.get("CLASH_CONFIG_PATH")
        self.controller_url = controller_url
        self.proxies: List[Dict] = []
        self.load_proxies()

    def load_proxies(self):
        """Loads proxy definitions from the specified YAML config file."""
        if not self.config_path or not os.path.exists(self.config_path):
            print("No Clash config found. Proxy disabled.")
            return

        try:
            with open(self.config_path, 'r') as f:
                data = yaml.safe_load(f)
                self.proxies = data.get('proxies', [])
                print(f"Loaded {len(self.proxies)} proxies from {self.config_path}")
        except Exception as e:
            print(f"Error loading proxy config: {e}")

    def rotate_proxy(self, selector_name: str = "Proxy"):
        """Randomly selects a proxy node and instructs Clash to switch to it.

        Args:
            selector_name: The name of the Clash proxy group selector.
        """
        if not self.proxies:
            return

        node = random.choice(self.proxies)
        node_name = node['name']
        
        try:
            # Clash External Controller API
            url = f"{self.controller_url}/proxies/{selector_name}"
            payload = {"name": node_name}
            
            # Use a short timeout to prevent hanging
            resp = requests.put(url, json=payload, timeout=2)
            if resp.status_code == 204:
                pass
            else:
                print(f"Failed to rotate proxy. Status: {resp.status_code}")
        except Exception as e:
            pass
