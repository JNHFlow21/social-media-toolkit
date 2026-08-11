"""MCP transport package for Social Media Toolkit."""

__version__ = "0.4.0"
__author__ = "JNHFlow21"
__email__ = "JNHFlow21@users.noreply.github.com"


def main():
    from .server import main as server_main

    return server_main()

__all__ = ["main"]
