"""Contract tests for `mmc_base.entrypoint._import_analyser`.

Failures at parse or import time must surface with a message naming
``MMC_ANALYSER_MODULE`` and the offending value — a container that dies
silently is the pain these tests exist to prevent.
"""

from __future__ import annotations

import pytest
from mmc_base.entrypoint import _import_analyser


@pytest.mark.parametrize("spec", ["no_colon", ""], ids=["no_colon", "empty"])
def test_malformed_spec_missing_colon_raises_value_error(spec: str) -> None:
    """Specs without a ``:`` must raise ``ValueError`` naming both var and value."""
    with pytest.raises(ValueError, match=r"MMC_ANALYSER_MODULE") as exc:
        _import_analyser(spec)
    assert repr(spec) in str(exc.value)


def test_trailing_colon_propagates_import_error() -> None:
    """``"trailing:"`` parses but the empty module name must not be silently swallowed."""
    with pytest.raises((ModuleNotFoundError, ImportError, ValueError)):
        _import_analyser("trailing:")


def test_missing_module_raises_import_error() -> None:
    """Well-formed spec but missing module → ``ModuleNotFoundError`` propagates unswallowed."""
    with pytest.raises(ModuleNotFoundError):
        _import_analyser("mmc_base_does_not_exist_xyz:Whatever")


def test_missing_class_on_real_module_raises_attribute_error() -> None:
    """Well-formed spec, module exists, class absent → ``AttributeError`` propagates."""
    with pytest.raises(AttributeError):
        _import_analyser("mmc_base.entrypoint:ClassThatDoesNotExist")
