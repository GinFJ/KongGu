from app.gui.task_runner import BackgroundTaskError, run_background_task


class ImmediateOwner:
    def after(self, _delay_ms, callback):
        callback()


def test_run_background_task_calls_success_callback():
    seen = []

    thread = run_background_task(
        owner=ImmediateOwner(),
        task=lambda: "ok",
        on_success=seen.append,
        on_error=lambda error: seen.append(error),
    )
    thread.join(timeout=5)

    assert seen == ["ok"]


def test_run_background_task_calls_error_callback_with_traceback():
    seen = []

    def fail():
        raise ValueError("boom")

    thread = run_background_task(
        owner=ImmediateOwner(),
        task=fail,
        on_success=lambda result: seen.append(result),
        on_error=seen.append,
    )
    thread.join(timeout=5)

    assert len(seen) == 1
    assert isinstance(seen[0], BackgroundTaskError)
    assert isinstance(seen[0].exception, ValueError)
    assert "ValueError: boom" in seen[0].detail
