"""Shared test setup."""

import os
import sys
import time

import pytest


@pytest.fixture(autouse=True)
def retry_windows_replace(monkeypatch):
    """Retry ``os.replace`` when Windows refuses a rename.

    Windows denies renaming a file or directory that antivirus or the search
    indexer briefly holds open, which fails unrelated tests at random. This is
    a test-environment artefact, so the retry lives here and never in
    production code.
    """
    if sys.platform != "win32":
        return
    real_replace = os.replace

    def replace_with_retry(source, target, *args, **kwargs):
        for attempt in range(5):
            try:
                return real_replace(source, target, *args, **kwargs)
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)

    monkeypatch.setattr(os, "replace", replace_with_retry)
