import unittest
from unittest.mock import MagicMock
from src.proxy.hook import RequestHook
from src.proxy.manager import ProxyManager

class TestRequestHook(unittest.TestCase):
    def test_get_headers(self):
        manager = MagicMock(spec=ProxyManager)
        hook = RequestHook(manager)
        
        headers = hook.get_headers()
        self.assertIn("User-Agent", headers)
        self.assertTrue(len(headers["User-Agent"]) > 0)

    def test_before_request_rotates_proxy(self):
        manager = MagicMock(spec=ProxyManager)
        hook = RequestHook(manager)
        
        hook.before_request()
        manager.rotate_proxy.assert_called_once()
        
    def test_get_session(self):
        manager = MagicMock(spec=ProxyManager)
        hook = RequestHook(manager)
        
        session = hook.get_session()
        self.assertIn("http", session.proxies)
        self.assertEqual(session.proxies["http"], "http://127.0.0.1:7890")

if __name__ == '__main__':
    unittest.main()