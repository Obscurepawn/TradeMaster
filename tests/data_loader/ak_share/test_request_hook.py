import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from utils.request_hook import (
    get_random_user_agent,
    set_current_user_agent,
    get_current_user_agent,
    update_headers_with_user_agent,
    hooked_get,
    hooked_post,
    hooked_request,
    install_hooks,
    uninstall_hooks,
    RequestHookContext,
    _original_get,
    _original_post,
    _original_request,
    USER_AGENTS
)

class TestRequestHook(unittest.TestCase):
    """Test cases for request hook functionality"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Store original methods
        self.original_get = _original_get
        self.original_post = _original_post
        self.original_request = _original_request

        # Uninstall hooks to ensure clean state
        uninstall_hooks()

        # Clear current User-Agent
        set_current_user_agent(None)

    def tearDown(self):
        """Tear down test fixtures after each test method."""
        # Restore original methods
        uninstall_hooks()

        # Clear current User-Agent
        set_current_user_agent(None)

    def test_get_random_user_agent(self):
        """Test getting a random User-Agent"""
        user_agent = get_random_user_agent()
        self.assertIn(user_agent, USER_AGENTS)
        self.assertIsInstance(user_agent, str)
        self.assertGreater(len(user_agent), 0)

    def test_set_and_get_current_user_agent(self):
        """Test setting and getting current User-Agent"""
        test_user_agent = "Test User Agent"
        set_current_user_agent(test_user_agent)
        self.assertEqual(get_current_user_agent(), test_user_agent)

    def test_get_current_user_agent_none(self):
        """Test getting current User-Agent when none is set"""
        self.assertIsNone(get_current_user_agent())

    def test_update_headers_with_user_agent_no_headers(self):
        """Test updating headers with User-Agent when no headers provided"""
        headers = update_headers_with_user_agent()
        self.assertIsInstance(headers, dict)
        self.assertIn("User-Agent", headers)
        self.assertIn(headers["User-Agent"], USER_AGENTS)

    def test_update_headers_with_user_agent_existing_headers(self):
        """Test updating headers with User-Agent when headers provided"""
        original_headers = {"Accept": "application/json"}
        headers = update_headers_with_user_agent(original_headers)
        self.assertIn("User-Agent", headers)
        self.assertIn("Accept", headers)
        self.assertIn(headers["User-Agent"], USER_AGENTS)

    def test_update_headers_with_user_agent_existing_user_agent(self):
        """Test updating headers when User-Agent already exists"""
        # Set current User-Agent first
        set_current_user_agent("Existing User Agent")
        original_headers = {"Other-Header": "Value"}
        headers = update_headers_with_user_agent(original_headers)
        # Should use the current User-Agent
        self.assertEqual(headers["User-Agent"], "Existing User Agent")

    @patch('utils.request_hook._original_get')
    def test_hooked_get(self, mock_original_get):
        """Test hooked GET request"""
        mock_response = MagicMock()
        mock_original_get.return_value = mock_response

        # Install hooks to test
        install_hooks()

        response = hooked_get("http://example.com")
        mock_original_get.assert_called_once()
        self.assertEqual(response, mock_response)

    @patch('utils.request_hook._original_post')
    def test_hooked_post(self, mock_original_post):
        """Test hooked POST request"""
        mock_response = MagicMock()
        mock_original_post.return_value = mock_response

        # Install hooks to test
        install_hooks()

        response = hooked_post("http://example.com")
        mock_original_post.assert_called_once()
        self.assertEqual(response, mock_response)

    @patch('utils.request_hook._original_request')
    def test_hooked_request(self, mock_original_request):
        """Test hooked session request"""
        mock_response = MagicMock()
        mock_original_request.return_value = mock_response

        # Install hooks to test
        install_hooks()

        session = MagicMock()
        response = hooked_request(session, "GET", "http://example.com")
        mock_original_request.assert_called_once()
        self.assertEqual(response, mock_response)

    def test_install_hooks(self):
        """Test installing hooks"""
        # Ensure hooks are not installed
        uninstall_hooks()

        # Install hooks
        install_hooks()

        # Check that methods have been replaced
        import requests
        self.assertNotEqual(requests.get, self.original_get)
        self.assertNotEqual(requests.post, self.original_post)
        self.assertNotEqual(requests.Session.request, self.original_request)

    def test_uninstall_hooks(self):
        """Test uninstalling hooks"""
        # Install hooks first
        install_hooks()

        # Uninstall hooks
        uninstall_hooks()

        # Check that methods have been restored
        import requests
        self.assertEqual(requests.get, self.original_get)
        self.assertEqual(requests.post, self.original_post)
        self.assertEqual(requests.Session.request, self.original_request)

    def test_request_hook_context(self):
        """Test RequestHookContext context manager"""
        test_user_agent = "Test User Agent"

        # Test with specific User-Agent
        with RequestHookContext(test_user_agent) as ctx:
            self.assertEqual(get_current_user_agent(), test_user_agent)
            self.assertEqual(ctx.user_agent, test_user_agent)

        # Test with random User-Agent
        with RequestHookContext() as ctx:
            self.assertIsNotNone(get_current_user_agent())
            self.assertIn(ctx.user_agent, USER_AGENTS)

    def test_request_hook_context_nested(self):
        """Test nested RequestHookContext"""
        outer_user_agent = "Outer User Agent"
        inner_user_agent = "Inner User Agent"

        with RequestHookContext(outer_user_agent) as outer_ctx:
            self.assertEqual(get_current_user_agent(), outer_user_agent)

            with RequestHookContext(inner_user_agent) as inner_ctx:
                self.assertEqual(get_current_user_agent(), inner_user_agent)
                self.assertEqual(inner_ctx.user_agent, inner_user_agent)

            # After inner context exits, should be back to outer
            self.assertEqual(get_current_user_agent(), outer_user_agent)

        # After outer context exits, should be None
        self.assertIsNone(get_current_user_agent())


if __name__ == '__main__':
    unittest.main()
