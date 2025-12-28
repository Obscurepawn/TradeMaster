import unittest
from unittest.mock import patch, MagicMock
import requests
from src.proxy.hook import GlobalRequestHook

class TestHook(unittest.TestCase):
    @patch('src.proxy.manager.requests.get')
    def test_patched_request_logic(self, mock_get):
        """Verify that headers and proxies are injected by the hook logic."""
        # Mock ProxyManager API response
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"proxies": {"Node": {"type": "SS"}}}
        
        hook = GlobalRequestHook()
        mock_session = MagicMock(spec=requests.Session)
        
        with patch('src.proxy.hook._ORIGINAL_REQUEST') as mock_orig:
            mock_orig.return_value = MagicMock(status_code=200)
            
            hook.patched_request(mock_session, "GET", "http://example.com", headers={"X-Test": "Value"})
            
            self.assertTrue(mock_orig.called)
            args, kwargs = mock_orig.call_args
            headers = kwargs.get("headers")
            proxies = kwargs.get("proxies")
            
            self.assertIn("User-Agent", headers)
            self.assertIn("sec-ch-ua", headers)
            self.assertEqual(headers["X-Test"], "Value")
            self.assertEqual(proxies["http"], "http://127.0.0.1:7890")

    @patch('src.proxy.manager.requests.get')
    @patch('src.proxy.hook.time.sleep')
    def test_jitter_logic(self, mock_sleep, mock_get):
        """Verify that jitter (sleep) is called in the hook logic."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"proxies": {"Node": {"type": "SS"}}}
        
        hook = GlobalRequestHook()
        mock_session = MagicMock(spec=requests.Session)
        
        with patch('src.proxy.hook._ORIGINAL_REQUEST') as mock_orig:
            mock_orig.return_value = MagicMock(status_code=200)
            hook.patched_request(mock_session, "GET", "http://example.com")
            self.assertTrue(mock_sleep.called)

if __name__ == "__main__":
    unittest.main()