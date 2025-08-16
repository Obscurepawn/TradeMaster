import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import yaml

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from proxy.clash.proxy import ClashConfigParser, ClashController

class TestClashConfigParser(unittest.TestCase):
    """Test cases for ClashConfigParser class"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.test_config_content = """
external-controller: localhost:9090
secret: test-secret
        """
        self.config_file_path = "/tmp/test_config.yaml"

        # Create a temporary config file for testing
        with open(self.config_file_path, 'w') as f:
            f.write(self.test_config_content)

    def tearDown(self):
        """Tear down test fixtures after each test method."""
        if os.path.exists(self.config_file_path):
            os.remove(self.config_file_path)

    def test_parse_config_success(self):
        """Test successful config parsing"""
        host, port, secret = ClashConfigParser.parse_config(self.config_file_path)
        self.assertEqual(host, "localhost")
        self.assertEqual(port, 9090)
        self.assertEqual(secret, "test-secret")

    def test_parse_config_file_not_found(self):
        """Test config parsing when file not found"""
        with self.assertRaises(FileNotFoundError):
            ClashConfigParser.parse_config("/path/that/does/not/exist.yaml")

    def test_parse_config_without_secret(self):
        """Test config parsing when secret is not provided"""
        config_content = """
external-controller: localhost:9090
        """
        config_file = "/tmp/test_config_no_secret.yaml"

        with open(config_file, 'w') as f:
            f.write(config_content)

        try:
            host, port, secret = ClashConfigParser.parse_config(config_file)
            self.assertEqual(host, "localhost")
            self.assertEqual(port, 9090)
            self.assertEqual(secret, "")
        finally:
            if os.path.exists(config_file):
                os.remove(config_file)

    def test_parse_config_without_external_controller(self):
        """Test config parsing when external-controller is not provided"""
        config_content = """
secret: test-secret
        """
        config_file = "/tmp/test_config_no_controller.yaml"

        with open(config_file, 'w') as f:
            f.write(config_content)

        try:
            host, port, secret = ClashConfigParser.parse_config(config_file)
            self.assertEqual(host, "localhost")
            self.assertEqual(port, 9090)
            self.assertEqual(secret, "test-secret")
        finally:
            if os.path.exists(config_file):
                os.remove(config_file)


class TestClashController(unittest.TestCase):
    """Test cases for ClashController class"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.controller = ClashController("localhost", 9090, "test-secret")

    @patch('proxy.clash.proxy.requests.get')
    def test_get_proxies_success(self, mock_get):
        """Test successful proxy retrieval"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "proxies": {
                "GLOBAL": {"type": "Selector", "all": ["Proxy1", "Proxy2"]},
                "Proxy1": {"type": "Shadowsocks"},
                "Proxy2": {"type": "VMess"}
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        proxies = self.controller.get_proxies()
        self.assertIsInstance(proxies, dict)
        self.assertIn("GLOBAL", proxies)

    @patch('proxy.clash.proxy.requests.get')
    def test_get_proxies_request_exception(self, mock_get):
        """Test proxy retrieval when request fails"""
        mock_get.side_effect = Exception("Connection failed")

        proxies = self.controller.get_proxies()
        self.assertEqual(proxies, {})

    @patch('proxy.clash.proxy.requests.get')
    def test_get_available_nodes_success(self, mock_get):
        """Test successful node retrieval"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "proxies": {
                "GLOBAL": {"type": "Selector", "all": ["Proxy1", "Proxy2", "DIRECT"]},
                "Proxy1": {"type": "Shadowsocks"},
                "Proxy2": {"type": "VMess"}
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        nodes = self.controller.get_available_nodes("GLOBAL")
        self.assertEqual(nodes, ["Proxy1", "Proxy2", "DIRECT"])

    @patch('proxy.clash.proxy.requests.get')
    def test_get_available_nodes_group_not_found(self, mock_get):
        """Test node retrieval when group not found"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "proxies": {
                "Proxy1": {"type": "Shadowsocks"},
                "Proxy2": {"type": "VMess"}
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        nodes = self.controller.get_available_nodes("GLOBAL")
        self.assertEqual(nodes, [])

    @patch('proxy.clash.proxy.requests.get')
    def test_get_available_nodes_not_selector_type(self, mock_get):
        """Test node retrieval when group is not selector type"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "proxies": {
                "GLOBAL": {"type": "Shadowsocks", "all": ["Proxy1", "Proxy2"]},
                "Proxy1": {"type": "Shadowsocks"},
                "Proxy2": {"type": "VMess"}
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        nodes = self.controller.get_available_nodes("GLOBAL")
        self.assertEqual(nodes, [])

    @patch('proxy.clash.proxy.requests.put')
    def test_switch_proxy_success(self, mock_put):
        """Test successful proxy switching"""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_put.return_value = mock_response

        result = self.controller.switch_proxy("GLOBAL", "Proxy1")
        self.assertTrue(result)

    @patch('proxy.clash.proxy.requests.put')
    def test_switch_proxy_failure(self, mock_put):
        """Test proxy switching when request fails"""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_put.return_value = mock_response

        result = self.controller.switch_proxy("GLOBAL", "Proxy1")
        self.assertFalse(result)

    @patch('proxy.clash.proxy.requests.put')
    def test_switch_proxy_exception(self, mock_put):
        """Test proxy switching when exception occurs"""
        mock_put.side_effect = Exception("Connection failed")

        with self.assertRaises(Exception):
            self.controller.switch_proxy("GLOBAL", "Proxy1")

    @patch('proxy.clash.proxy.ClashController.get_available_nodes')
    @patch('proxy.clash.proxy.ClashController.switch_proxy')
    def test_change_random_proxy_success(self, mock_switch_proxy, mock_get_available_nodes):
        """Test successful random proxy change"""
        mock_get_available_nodes.return_value = ["Proxy1", "Proxy2", "GLOBAL", "DIRECT"]
        mock_switch_proxy.return_value = True

        self.controller.change_random_proxy("GLOBAL")
        mock_get_available_nodes.assert_called_once_with("GLOBAL")
        mock_switch_proxy.assert_called_once()

    @patch('proxy.clash.proxy.ClashController.get_available_nodes')
    def test_change_random_proxy_no_nodes(self, mock_get_available_nodes):
        """Test random proxy change when no nodes available"""
        mock_get_available_nodes.return_value = []

        self.controller.change_random_proxy("GLOBAL")
        mock_get_available_nodes.assert_called_once_with("GLOBAL")

    @patch('proxy.clash.proxy.ClashController.get_available_nodes')
    def test_change_random_proxy_no_filtered_nodes(self, mock_get_available_nodes):
        """Test random proxy change when no filtered nodes available"""
        mock_get_available_nodes.return_value = ["GLOBAL", "DIRECT"]

        self.controller.change_random_proxy("GLOBAL")
        mock_get_available_nodes.assert_called_once_with("GLOBAL")


if __name__ == '__main__':
    unittest.main()
