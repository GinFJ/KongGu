import os

import pytest


@pytest.fixture(autouse=True)
def isolate_runtime_environment():
    isolated_names = (
        "KONGGU_SEMESTER_START_DATE",
        "KONGGU_TEACHING_WEEKS",
        "KONGGU_OCR_MODEL_DIR",
    )
    for name in isolated_names:
        os.environ.pop(name, None)
    yield
    for name in isolated_names:
        os.environ.pop(name, None)

