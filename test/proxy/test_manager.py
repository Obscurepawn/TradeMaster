import unittest
from unittest.mock import patch, mock_open
from src.proxy.manager import ProxyManager


class TestProxyManager(unittest.TestCase):
    @patch("src.proxy.manager.requests.get")
    def test_load_proxies_from_api(self, mock_get):
        """Verify that proxies are correctly loaded from the API."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "proxies": {
                "Node A": {"type": "Shadowsocks"},
                "Node B": {"type": "Vmess"},
                "Selector Group": {"type": "Selector"}
            }
        }
        
        manager = ProxyManager(api_url="http://127.0.0.1:9090")
        self.assertTrue(manager.is_enabled)
        self.assertEqual(len(manager.proxies), 2)
        self.assertIn("Node A", manager.proxies)
        self.assertIn("Node B", manager.proxies)
        self.assertNotIn("Selector Group", manager.proxies)

    @patch("src.proxy.manager.ProxyManager.check_node_health")
    @patch("src.proxy.manager.requests.put")
    @patch("src.proxy.manager.requests.get")
    def test_rotate_proxy(self, mock_get, mock_put, mock_health):
        # Setup manager with fake proxies
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"proxies": {"Node A": {"type": "SS"}}}
        mock_health.return_value = True
        
        manager = ProxyManager(selector_name="🚀 节点选择")
        manager.is_enabled = True
        manager.proxies = ["Node A", "Node B"]

        mock_put.return_value.status_code = 204

        manager.rotate_proxy()

        mock_put.assert_called_once()
        args, kwargs = mock_put.call_args
        self.assertIn("/proxies/%F0%9F%9A%80%20%E8%8A%82%E7%82%B9%E9%80%89%E6%8B%A9", args[0])
        self.assertIn(kwargs['json']['name'], ["Node A", "Node B"])

    @patch("src.proxy.manager.requests.get")
    def test_missing_config_path(self, mock_get):
        """Verify behavior when API connection fails."""
        mock_get.side_effect = Exception("Connection refused")
        with patch("src.proxy.manager.logger.error") as mock_error:
            manager = ProxyManager(api_url="http://invalid:9090")
            self.assertFalse(manager.is_enabled)
            # Check that an error was logged about connection failure
            self.assertTrue(any("Failed to connect to Clash API" in call[0][0] for call in mock_error.call_args_list))

    @patch("src.proxy.manager.requests.put")
    @patch("src.proxy.manager.requests.get")
    def test_rotate_skipped_when_disabled(self, mock_get, mock_put):
        """Verify rotate_proxy returns early if disabled."""
        mock_get.side_effect = Exception("Connection refused")
        manager = ProxyManager(api_url="http://invalid:9090")
        self.assertFalse(manager.is_enabled)
        manager.rotate_proxy()
        mock_put.assert_not_called()


if __name__ == '__main__':
    unittest.main()
