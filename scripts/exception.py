class ConfigError(Exception):
    """Raised when a required setting is missing or invalid."""

class UnsupportedFormatError(Exception):
    """Raised when an SOP file is in a format we cannot parse."""