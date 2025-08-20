"""Set up test fixtures before each test method."""
        # Initialize with a mock config path
from config.config_loader import init_config_loader


try:
    init_config_loader("test/test_config.yaml")
except FileNotFoundError:
    # If the config file doesn't exist, we'll test with mocks
    pass
