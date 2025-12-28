# Config Agent Context

## Responsibility
Handles loading and validation of the configuration file.

## Implementation
- `settings.py`: Loads YAML and provides a typed Config object.
- `schema.py`: Defines the expected structure and types.

## Testing
- Verify invalid YAML throws errors.
- Verify missing fields throw validation errors.
- Verify environment variable substitution (if applicable).
