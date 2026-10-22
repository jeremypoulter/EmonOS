"""Host-only checks for the failure-evidence helpers used by test_app_stack."""

from types import SimpleNamespace

from common import console_log_text, save_report_file, summarize_console, wait_for_app


REBOOTED = (
    "\x1b[2J  Booting `EmonOS slot A'\r\nBooting Slot A\r\n"
    "[  OK  ] Stopped EmonOS bounded application health and slot commit.\r\n"
    "reboot: Restarting system\r\n"
    "Booting Slot A\r\n"
)


def test_console_summary_counts_boots_and_health_monitor_stops():
    assert summarize_console(REBOOTED) == {
        "boots": 2, "restarts": 1, "health_monitor_stops": 1,
    }
    assert summarize_console("") == {"boots": 0, "restarts": 0, "health_monitor_stops": 0}


def test_console_log_text_is_empty_when_the_log_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("EMONOS_TEST_REPORT_DIR", str(tmp_path))
    assert console_log_text("x86-64-vm") == ""
    (tmp_path / "labgrid").mkdir()
    (tmp_path / "labgrid/console_main").write_text("hello")
    assert console_log_text("x86-64-vm") == "hello"


def make_command(outputs):
    samples = iter(outputs)

    def run_check(_text, timeout=None):
        value = next(samples)
        if isinstance(value, Exception):
            raise value
        return [value]

    return SimpleNamespace(run_check=run_check)


def test_wait_for_app_returns_the_timeline_when_the_app_starts():
    command = make_command([
        "up=50 docker=active preload=activating app=inactive health=activating load=3",
        "up=250 docker=active preload=active app=active health=active load=1",
    ])
    started, timeline, reason = wait_for_app(command, budget=60.0, interval=0.0)
    assert started and reason == "started" and len(timeline) == 2


def test_wait_for_app_reports_a_lost_console_with_the_samples_so_far():
    command = make_command([
        "up=280 docker=active preload=active app=activating health=activating load=4",
        TimeoutError("console went away"),
    ])
    started, timeline, reason = wait_for_app(command, budget=60.0, interval=0.0)
    assert not started
    assert reason.startswith("console lost")
    assert timeline == ["up=280 docker=active preload=active app=activating health=activating load=4"]


def test_wait_for_app_gives_up_after_its_budget():
    command = make_command(["up=1 docker=active preload=active app=failed health=activating load=0"] * 100)
    started, timeline, reason = wait_for_app(command, budget=0.5, interval=0.01)
    assert not started and "not active after" in reason and timeline


def test_save_report_file_writes_next_to_the_report_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("EMONOS_TEST_REPORT_DIR", str(tmp_path / "report"))
    save_report_file("x86-64-vm", "timeline.txt", "a\nb\n")
    assert (tmp_path / "report/timeline.txt").read_text() == "a\nb\n"
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory")
    monkeypatch.setenv("EMONOS_TEST_REPORT_DIR", str(blocked))
    save_report_file("x86-64-vm", "timeline.txt", "ignored")
