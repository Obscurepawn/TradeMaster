import random
from dataclasses import dataclass
from typing import Dict


@dataclass
class BrowserFingerprint:
    """Represents a consistent set of browser headers for a request."""
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_platform: str
    accept_language: str = "zh-CN,zh;q=0.9,en;q=0.8"
    sec_fetch_site: str = "same-origin"
    sec_fetch_mode: str = "navigate"
    sec_fetch_dest: str = "document"

    def to_headers(self) -> Dict[str, str]:
        """Converts the fingerprint to a dictionary of HTTP headers."""
        return {
            "User-Agent": self.user_agent,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{self.sec_ch_ua_platform}"',
            "Accept-Language": self.accept_language,
            "sec-fetch-site": self.sec_fetch_site,
            "sec-fetch-mode": self.sec_fetch_mode,
            "sec-fetch-dest": self.sec_fetch_dest,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Upgrade-Insecure-Requests": "1"
        }


# Modern browser profiles
PROFILES = [
    # Chrome 120 on Windows
    BrowserFingerprint(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        sec_ch_ua='"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        sec_ch_ua_platform="Windows"
    ),
    # Chrome 120 on macOS
    BrowserFingerprint(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        sec_ch_ua='"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        sec_ch_ua_platform="macOS"
    ),
    # Edge 120 on Windows
    BrowserFingerprint(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        sec_ch_ua='"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
        sec_ch_ua_platform="Windows"
    )
]


def get_random_fingerprint() -> BrowserFingerprint:
    """Returns a random BrowserFingerprint profile."""
    return random.choice(PROFILES)
