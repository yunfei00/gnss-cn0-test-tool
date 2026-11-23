import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    APP_VERSION = version("gnss-cn0-test-tool")
except PackageNotFoundError:
    source = Path(__file__).resolve().parents[2] / "pyproject.toml"
    APP_VERSION = tomllib.loads(source.read_text(encoding="utf-8"))["project"]["version"] if source.is_file() else "unknown"
