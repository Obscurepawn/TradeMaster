#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Clash Proxy Controller Example

This example demonstrates how to use the Clash proxy controller to:
1. Parse Clash configuration file
2. Connect to Clash API
3. Get available proxy nodes
4. Switch to a random proxy node
"""

import sys
import os

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from config.config_loader import init_config_loader, get_config
from proxy.clash.proxy import ClashConfigParser, ClashController


def main():
    """Main function to demonstrate Clash proxy controller usage"""
    try:
        # Initialize configuration loader with examples config
        init_config_loader("examples/config/config.yaml")

        # Get configuration
        config_path = get_config().get_clash_config_path()
        host, port, secret = ClashConfigParser.parse_config(config_path)
        controller = ClashController("localhost", port, secret)

        # Get available nodes
        group_name = "GLOBAL"
        nodes = controller.get_available_nodes(group_name)
        print(f"Available nodes in {group_name}: {nodes}")

        # Change to a random proxy
        success = controller.change_random_proxy(group_name)
        if success:
            print("Successfully switched to a random proxy")
        else:
            print("Fail to switch to a random proxy")

    except Exception as e:
        print(f"Error occurred: {e}")


if __name__ == "__main__":
    main()
