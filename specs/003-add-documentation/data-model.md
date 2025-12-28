# Data Model: Documentation

This feature does not change the runtime data model, but it codifies the "Documentation Model" used across the project.

## Docstring Template (Google Style)

```python
def function_name(param1: type, param2: type) -> return_type:
    """Short summary of the function.

    Longer description explaining the purpose, where it's called from
    (e.g., "Invoked by the BacktestEngine during the daily loop"),
    and any side effects.

    Args:
        param1: Semantics of param1.
        param2: Semantics of param2.

    Returns:
        Description of the returned object.

    Raises:
        ValueError: Description of when this error occurs.
    """
```
