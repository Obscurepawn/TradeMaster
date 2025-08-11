from typing import Tuple
import requests
import random
import yaml


DEFAULT_CONFIG_PATH = "/mnt/c/Users/10255/.config/clash/config.yaml"
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
            controller = config.get("external-controller", f"{LOCAL_HOST}:9090")
            if ":" in controller:
                host, port_str = controller.split(":", 1)
                port = int(port_str)
            else:
                host = controller
                port = 9090
            secret = config.get("secret", "")
            print(f"param from config_file. host={host}, port={port}, secret={secret}")
            return LOCAL_HOST, port, secret  # only support localhost for now
        except FileNotFoundError as e:
            print(f"file not file. config_path={config_path}")
            raise e
        except Exception as e:
            print(f"fail to parse config. err={str(e)}")
            raise e


class ClashController:
    def __init__(self, host, port, secret):
        self.base_url = f"{host}:{port}"
        self.headers = {"Authorization": f"Bearer {secret}"} if secret else {}

    def get_proxies(self) -> dict:
        url = f"http://{self.base_url}/proxies"
        try:
            response = requests.get(url, headers=self.headers, timeout=5)
            response.raise_for_status()
            return response.json().get("proxies", {})
        except requests.exceptions.RequestException as e:
            print(f"fail to request. err={e}")
            return {}

    def get_available_nodes(self, group_name: str) -> list:
        proxies = self.get_proxies()
        print(f"proxies={proxies}")
        group = proxies.get(group_name)

        if not group:
            print(f"'{group_name}' not found in proxies")
            return []

        if group["type"] != "Selector":
            print(f"'{group_name}' is not a proxy group")
            return []

        return group.get("all", [])

    def switch_proxy(self, group_name: str, node_name: str):
        url = f"http://{self.base_url}/proxies/{group_name}"
        data = {"name": node_name}

        try:
            response = requests.put(url, json=data, headers=self.headers, timeout=5)
            if response.status_code == 204:
                print(f"successfully switch to {node_name}")
                return True
            print(
                f"fail to switch proxy ip. code={response.status_code}. err_msg={response.text}"
            )
        except requests.exceptions.RequestException as e:
            print(
                f"fail to request. err={e}. group_name={group_name}, node_name={node_name}"
            )
            raise e

    def change_random_proxy(self, group_name=GLOBAL):
        nodes = self.get_available_nodes(group_name)
        if not nodes:
            print("no available nodes found")
            return False

        filtered_nodes = [n for n in nodes if n != group_name and n != "DIRECT"]

        if not filtered_nodes:
            print("no available nodes to switch to")
            return False

        selected = random.choice(filtered_nodes)
        print(f"random_selected_proxy={selected}")
        self.switch_proxy(group_name, selected)


if __name__ == "__main__":
    host, port, secret = ClashConfigParser.parse_config(DEFAULT_CONFIG_PATH)
    controller = ClashController("localhost", port, secret)
    controller.change_random_proxy(GLOBAL)
