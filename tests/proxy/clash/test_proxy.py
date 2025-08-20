#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test cases for Clash proxy module
"""

import unittest
import sys
import os
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from proxy.clash.proxy import ClashController


class TestProxy(unittest.TestCase):
    """Test cases for Clash proxy controller"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.proxy_controller = ClashController("localhost", 9090, "test-secret")

    @patch('proxy.clash.proxy.requests.get')
    def test_get_proxies_success(self, mock_get):
        """Test getting proxies list successfully"""
        # Mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "proxies": {
                "Proxy1": {"type": "ss", "now": "Proxy1"},
                "Proxy2": {"type": "vmess", "now": "Proxy2"}
            }
        }
        mock_get.return_value = mock_response

        proxies = self.proxy_controller.get_proxies()
        self.assertEqual(len(proxies), 2)
        self.assertIn("Proxy1", proxies)
        self.assertIn("Proxy2", proxies)

    @patch('proxy.clash.proxy.requests.get')
    def test_get_proxies_failure(self, mock_get):
        """Test getting proxies list with failure"""
        mock_get.side_effect = Exception("Connection failed")

        proxies = self.proxy_controller.get_proxies()
        self.assertEqual(proxies, {})

    @patch('proxy.clash.proxy.requests.put')
    @patch('proxy.clash.proxy.requests.get')
    @patch('proxy.clash.proxy.random.choice')
    def test_change_random_proxy_success(self, mock_choice, mock_get, mock_put):
        """Test changing to a random proxy successfully"""
        # Mock proxies response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "proxies": {
                "GLOBAL": {
                    "type": "Selector",
                    "now": "Proxy1",
                    "all": ["Proxy1", "Proxy2", "Proxy3", "DIRECT"]
                }
            }
        }
        mock_get.return_value = mock_response
        mock_put.return_value.status_code = 204
        mock_choice.return_value = "Proxy2"

        result = self.proxy_controller.change_random_proxy()
        self.assertTrue(result)
        mock_put.assert_called_once()

    def test_change_random_proxy_no_proxies(self):
        """Test changing to a random proxy when no proxies available"""
        # Mock proxies response with no available nodes
        with patch('proxy.clash.proxy.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "proxies": {
                    "GLOBAL": {
                        "type": "Selector",
                        "now": "DIRECT",
                        "all": ["DIRECT"]
                    }
                }
            }
            mock_get.return_value = mock_response

            with self.assertRaises(RuntimeError) as context:
                self.proxy_controller.change_random_proxy()
            self.assertIn("No available nodes to switch to", str(context.exception))

    @patch('proxy.clash.proxy.requests.put')
    @patch('proxy.clash.proxy.requests.get')
    def test_change_random_proxy_failure(self, mock_get, mock_put):
        """Test changing to a random proxy with failure"""
        # Mock proxies response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "proxies": {
                "GLOBAL": {
                    "type": "Selector",
                    "now": "Proxy1",
                    "all": ["Proxy1", "Proxy2", "DIRECT"]
                }
            }
        }
        mock_get.return_value = mock_response
        mock_put.side_effect = Exception("Connection failed")
        with patch('proxy.clash.proxy.random.choice', return_value="Proxy2"):
            with self.assertRaises(Exception) as context:
                self.proxy_controller.change_random_proxy()
            self.assertIn("Connection failed", str(context.exception))


if __name__ == "__main__":
    unittest.main()
