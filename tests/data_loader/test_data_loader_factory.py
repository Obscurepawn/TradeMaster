import unittest
from data_loader import DataLoaderFactory, DataLoader, AkshareDataLoader


class TestDataReaderFactory(unittest.TestCase):
    """Test cases for data loader factory"""

    def test_create_akshare_data_loader(self):
        """Test creating Akshare data loader"""
        loader = DataLoaderFactory.create_data_loader("akshare")
        self.assertIsInstance(loader, DataLoader)
        self.assertIsInstance(loader, AkshareDataLoader)

    def test_create_unsupported_data_loader(self):
        """Test creating unsupported data loader"""
        with self.assertRaises(ValueError) as context:
            DataLoaderFactory.create_data_loader("unsupported")

        self.assertIn("Unsupported data loader type", str(context.exception))


if __name__ == "__main__":
    unittest.main()
