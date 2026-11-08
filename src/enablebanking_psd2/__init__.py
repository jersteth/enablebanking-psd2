"""PSD2 account access to your own bank accounts via Enable Banking."""

import logging

__version__ = "0.2.0"

# A library must not print: without the CLI's handler, log records go nowhere (not to Python's lastResort).
logging.getLogger(__name__).addHandler(logging.NullHandler())


def __getattr__(name: str):  # lazy: importing the package (e.g. for --version) doesn't import the whole API
    if name in ("fetch_history", "consent_status"):
        from enablebanking_psd2 import api

        return getattr(api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
