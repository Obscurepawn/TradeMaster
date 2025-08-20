from .data_loader import constants
from .data_loader import DataLoader
from .ak_share.akshare_data_loader import AkshareDataLoader


class DataLoaderFactory:
    """Factory class for data loaders"""

    @staticmethod
    def create_data_loader(loader_type: str) -> DataLoader:
        """
        Creates a data loader instance based on the specified type

        Args:
            loader_type: Type of loader to create (currently only supports 'akshare')

        Returns:
            DataLoader: Instance of the requested data loader

        Raises:
            ValueError: When an unsupported loader type is requested
        """
        if loader_type.lower() == constants.AK_SHARE:
            return AkshareDataLoader()
        else:
            raise ValueError(
                f"Unsupported data loader type: {loader_type}. "
                "Currently only 'akshare' is supported."
            )
