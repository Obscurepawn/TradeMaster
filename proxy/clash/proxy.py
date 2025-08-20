from typing import Tuple
import requests
import random
import yaml

# Import logger module
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
from logger.logger import get_logger


logger = get_logger(__name__)

LOCAL_HOST = "localhost"
DEFAULT_PORT = 9090
DEFAULT_SECRET = ""
GLOBAL = "GLOBAL"


class ClashConfigParser:
    @staticmethod
    def parse_config(config_path: str) -> Tuple[str, int, str]:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            controller = config.get("external-controller", f"{LOCAL_HOST}:{DEFAULT_PORT}")
            if ":" in controller:
                host, port_str = controller.split(":", 1)
                port = int(port_str)
            else:
                host = controller
                port = DEFAULT_PORT
            secret = config.get("secret", "")
            logger.info(
                f"Parameters from config file. host={host}, port={port}, secret={secret}"
            )
            return LOCAL_HOST, port, secret  # only support localhost for now
            return LOCAL_HOST, port, secret  # only support localhost for now
        except FileNotFoundError as e:
            logger.error(f"File not found. config_path={config_path}")
            raise e
        except Exception as e:
            logger.error(f"Fail to parse config. err={str(e)}")
            raise e


class ClashController:
    def __init__(self, host: str, port: int, secret: str):
        self.base_url = f"{host}:{port}"
        self.headers = {"Authorization": f"Bearer {secret}"} if secret else {}

    def get_proxies(self) -> dict:
        url = f"http://{self.base_url}/proxies"
        try:
            response = requests.get(url, headers=self.headers, timeout=5)
            response.raise_for_status()
            return response.json().get("proxies", {})
        except Exception as e:  # Catch all exceptions, not just RequestException
            logger.error(f"Fail to make request. err={e}")
            return {}

    def get_available_nodes(self, group_name: str) -> list:
        proxies = self.get_proxies()
        logger.debug(f"proxies={proxies}")
        group = proxies.get(group_name)

        if not group:
            logger.warning(f"'{group_name}' not found in proxies")
            return []

        if group["type"] != "Selector":
            logger.warning(f"'{group_name}' is not a proxy group")
            return []

        return group.get("all", [])

    def switch_proxy(self, group_name: str, node_name: str):
        url = f"http://{self.base_url}/proxies/{group_name}"
        data = {"name": node_name}

        try:
            response = requests.put(url, json=data, headers=self.headers, timeout=5)
            if response.status_code == 204:
                logger.info(f"Successfully switched to {node_name}")
                return True
            logger.error(
                f"Fail to switch proxy IP. code={response.status_code}. err_msg={response.text}"
            )
            raise RuntimeError(f"Failed to switch proxy. Status code: {response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Fail to make request. err={e}. group_name={group_name}, node_name={node_name}"
            )
            raise e

    def change_random_proxy(self, group_name=GLOBAL):
        nodes = self.get_available_nodes(group_name)
        if not nodes:
            logger.warning("No available nodes found")
            raise RuntimeError("No available nodes found")

        filtered_nodes = [n for n in nodes if n != group_name and n != "DIRECT"]

        if not filtered_nodes:
            logger.warning("No available nodes to switch to")
            raise RuntimeError("No available nodes to switch to")

        selected = random.choice(filtered_nodes)
        logger.info(f"random_selected_proxy={selected}")
        return self.switch_proxy(group_name, selected)
