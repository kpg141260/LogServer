__VERSION__ = "0.1.0.8"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
"""Load TOML preferences with explicit command-line overrides."""
import argparse

from common.vt import __version_info__
from core.config import ConfigManager
from core.runtime import ApplicationSetup
from core.startup import ensureStartup, startupPaths


VERSION = __version_info__.version() if __version_info__.project_info else "0.1.0"


def load_config(argv=None) -> ApplicationSetup:
    parser = argparse.ArgumentParser(description="Cross-platform socket log server")
    parser.add_argument("-v", "--version", action="version", version=f"LogServer {VERSION}")
    parser.add_argument("--install", action="store_true", help="Open startup setup")
    parser.add_argument("--host", help="Bind IPv4 or IPv6 address")
    parser.add_argument("--port", type=int, help="Listening port")
    parser.add_argument("--protocol", choices=("tcp", "udp", "both"))
    args = parser.parse_args(argv)
    try:
        paths = startupPaths()
        ensureStartup(paths, force=args.install)
        overrides = {key: value for key, value in vars(args).items()
                     if key in ("host", "port", "protocol") and value is not None}
        manager = ConfigManager(paths.configPath(), overrides={"socket": overrides})
        manager.validate(manager.config)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        parser.error(str(exc))
    return ApplicationSetup(manager, paths)
