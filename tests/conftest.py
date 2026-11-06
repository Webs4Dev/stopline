# Settings shared by every test.
import os

import pytest


@pytest.fixture(autouse=True)
def _clean_git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the git settings of whatever launched the tests.

    Git hooks and `git -c` pass settings down through GIT_* variables. Inherited, they would
    change the answers from git in test repositories, or even point git at the real repository.
    """
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
