"""Tests for :mod:`mmc_base.lambda_handler` — Lambda entrypoint."""

from __future__ import annotations


def test_lambda_handler_exists():
    """Lambda handler module should exist and export handler."""
    from mmc_base.lambda_handler import handler

    assert callable(handler)


def test_lambda_handler_signature():
    """Lambda handler should accept (event, context) and return dict."""
    import inspect

    from mmc_base.lambda_handler import handler

    sig = inspect.signature(handler)
    params = list(sig.parameters.keys())
    assert "event" in params
    assert "context" in params
