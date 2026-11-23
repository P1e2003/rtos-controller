"""Bootstrap smoke tests verifying package imports."""

import plots
import sim


def test_package_imports() -> None:
    assert sim.__doc__ is not None
    assert plots.__doc__ is not None
