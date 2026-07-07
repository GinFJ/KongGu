import os

import pytest


@pytest.fixture(autouse=True)
def isolate_calendar_environment():
    os.environ.pop("KONGGU_SEMESTER_START_DATE", None)
    os.environ.pop("KONGGU_TEACHING_WEEKS", None)
    yield
    os.environ.pop("KONGGU_SEMESTER_START_DATE", None)
    os.environ.pop("KONGGU_TEACHING_WEEKS", None)

