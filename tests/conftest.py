"""Shared pytest fixtures for toolaccess tests."""

import pytest
from typer.testing import CliRunner

from toolaccess import InvocationContext


@pytest.fixture
def mock_ctx():
    """InvocationContext with surface='rest' for use in codec/renderer/pipeline tests."""
    return InvocationContext(surface="rest")


@pytest.fixture
def runner():
    """CliRunner for invoking CLI commands in tests."""
    return CliRunner()
