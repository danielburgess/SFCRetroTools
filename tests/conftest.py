"""Shared pytest fixtures + markers."""
from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "libsfx: test requires retrotool_libsfx (bundled toolchain) installed",
    )


@pytest.fixture(autouse=True)
def _reset_toolchain_cache():
    """Drop _toolchain lru_caches between tests so monkeypatched env vars apply."""
    from retrotool import _toolchain
    _toolchain.clear_cache()
    yield
    _toolchain.clear_cache()
