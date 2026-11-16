# Validate the package without Bluetooth hardware.
check:
    uv sync --frozen --group dev
    uv run --frozen ruff check src tests
    uv run --frozen ruff format --check src tests
    uv run --frozen pytest -q

# Build release archives.
package:
    uv build

# Build a local container image.
image:
    docker build -t bumble-ha-proxy .
