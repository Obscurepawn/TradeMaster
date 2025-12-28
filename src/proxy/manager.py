import os
import yaml
import random
import requests
from typing import List, Dict, Optional

class ProxyManager:
    def __init__(self, config_path: Optional[str] = None, controller_url: str = "http://127.0.0.1:9090"):
        self.config_path = config_path or os.environ.get("CLASH_CONFIG_PATH")
        self.controller_url = controller_url
        self.proxies: List[Dict] = []
        self.load_proxies()

    def load_proxies(self):
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
        """
        Selects a random node from the loaded config and instructs the local Clash instance to switch to it.
        """
        if not self.proxies:
            return

        node = random.choice(self.proxies)
        node_name = node['name']
        
        try:
            # Clash External Controller API
            url = f"{self.controller_url}/proxies/{selector_name}"
            payload = {"name": node_name}
            
            # We use a short timeout so we don't hang if Clash isn't running
            resp = requests.put(url, json=payload, timeout=2)
            if resp.status_code == 204:
                # print(f"Rotated Proxy to: {node_name}")
                pass
            else:
                print(f"Failed to rotate proxy. Status: {resp.status_code}")
        except Exception as e:
            # Fail silently to avoid spamming logs if Clash isn't running
            pass
