# Quickstart: Using the Global Logger

## Configuration
Add a `logging` section to your `config.yaml`:

```yaml
logging:
  level: "INFO"
  file_path: "logs/trade_master.log"
```

## Usage in Code
In any module within `src/`, import the standard logging module:

```python
import logging

logger = logging.getLogger(__name__)

def my_function():
    logger.info("This is an info message")
    logger.error("Something went wrong")
```

## Initialization
The logger is initialized in `main.py` using the loaded configuration:

```python
from src.config.logging_config import setup_logging
config = load_config(args.config)
setup_logging(config.logging)
```
