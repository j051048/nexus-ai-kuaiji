"""Opt-in pytest gate: required cases must execute, not skip or xfail."""

import pytest


class _CollectionGate:
    def __init__(self, config):
        self.config = config

    def pytest_collectreport(self, report):
        if report.skipped:
            self.config._qa_unexecuted = True


def pytest_configure(config):
    config._qa_unexecuted = False
    config.pluginmanager.register(_CollectionGate(config), "qa-collection-gate")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    report = result.get_result()
    if report.skipped or hasattr(report, "wasxfail"):
        item.config._qa_unexecuted = True


def pytest_sessionfinish(session, exitstatus):
    if session.testscollected == 0 or session.config._qa_unexecuted:
        reporter = session.config.pluginmanager.getplugin("terminalreporter")
        if reporter:
            reporter.write_line(
                "QA requires every selected case to execute: no skips, xfail or empty suites"
            )
        session.exitstatus = 1
