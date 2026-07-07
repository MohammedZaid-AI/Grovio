"""
Formatting utilities for displaying data to users consistently.
"""


def format_quantity(value, decimals=1):
    """
    Format a quantity (stock, weight, etc.) to fixed decimal places.

    Handles floating-point display artifacts by rounding to 1-2 decimals.

    Args:
        value: Numeric value to format
        decimals: Number of decimal places (default: 1)

    Returns:
        Formatted number (e.g., 7.2, 10.5, 42.0)
    """
    if value is None or value == "":
        return 0

    try:
        num = float(value)
        return round(num, decimals)
    except (TypeError, ValueError):
        return value
