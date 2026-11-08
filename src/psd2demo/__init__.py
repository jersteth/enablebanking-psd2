"""PSD2 account access for Belgian personal accounts via Enable Banking."""

import logging

__version__ = "0.1.0"

# A library must not print: without the CLI's handler, log records go nowhere (not to Python's lastResort).
logging.getLogger(__name__).addHandler(logging.NullHandler())
