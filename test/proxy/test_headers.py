import unittest
from src.proxy.headers import get_random_fingerprint, PROFILES


class TestHeaders(unittest.TestCase):
    def test_get_random_fingerprint(self):
        """Verify that a random fingerprint can be retrieved and has necessary fields."""
        fp = get_random_fingerprint()
        self.assertIsNotNone(fp.user_agent)
        self.assertIsNotNone(fp.sec_ch_ua)
        self.assertIn(fp.sec_ch_ua_platform, ["Windows", "macOS"])

    def test_to_headers(self):
        """Verify the header dictionary conversion."""
        fp = PROFILES[0]
        headers = fp.to_headers()
        self.assertEqual(headers["User-Agent"], fp.user_agent)
        self.assertEqual(headers["sec-ch-ua-platform"],
                         f'"{fp.sec_ch_ua_platform}"')
        self.assertIn("sec-ch-ua", headers)
        self.assertIn("Accept-Language", headers)

    def test_variety(self):
        """Roughly verify that we get different fingerprints over many calls."""
        agents = {get_random_fingerprint().user_agent for _ in range(20)}
        self.assertGreater(len(agents), 1)


if __name__ == "__main__":
    unittest.main()
