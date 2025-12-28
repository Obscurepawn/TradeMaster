import unittest
from unittest.mock import MagicMock, patch, mock_open
from src.proxy.manager import ProxyManager

class TestProxyManager(unittest.TestCase):
    def test_load_proxies(self):
        yaml_content = """
proxies:
  - name: Node A
    server: 1.1.1.1
  - name: Node B
    server: 2.2.2.2
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch("os.path.exists", return_value=True):
                manager = ProxyManager(config_path="dummy.yaml")
                self.assertEqual(len(manager.proxies), 2)
                self.assertEqual(manager.proxies[0]['name'], "Node A")

    @patch("src.proxy.manager.requests.put")
    def test_rotate_proxy(self, mock_put):
        # Setup manager with fake proxies
        manager = ProxyManager()
        manager.proxies = [{"name": "Node A"}, {"name": "Node B"}]
        
        mock_put.return_value.status_code = 204
        
        manager.rotate_proxy()
        
        mock_put.assert_called_once()
        args, kwargs = mock_put.call_args
        self.assertIn("/proxies/Proxy", args[0])
        self.assertIn(kwargs['json']['name'], ["Node A", "Node B"])

if __name__ == '__main__':
    unittest.main()