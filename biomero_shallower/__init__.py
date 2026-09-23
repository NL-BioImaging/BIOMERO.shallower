"""BIOMERO filesystem result shallower."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("biomero-shallower")
except PackageNotFoundError:
    __version__ = "0.0.dev0"
