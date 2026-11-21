import unittest

from cash_tracker import ConsoleUI, Tracker


class FakeDashboard:
    def __init__(self, start_result=True, urls=None, error=None):
        self.start_result = start_result
        self._urls = urls or ["http://127.0.0.1:8765/"]
        self.error = error
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1
        return self.start_result

    def stop(self):
        self.stops += 1

    def urls(self):
        return list(self._urls)


class TrackerDashboardTests(unittest.TestCase):
    def test_start_services_starts_dashboard_and_shutdown_stops_it(self):
        dashboard = FakeDashboard()
        tracker = Tracker(
            None,
            ui=ConsoleUI(plain=True),
            write_csv=False,
            dashboard=dashboard,
        )

        tracker.start_services()
        self.assertEqual(dashboard.starts, 1)

        tracker.shutdown()
        self.assertEqual(dashboard.stops, 1)

    def test_dashboard_failure_does_not_break_tracker(self):
        dashboard = FakeDashboard(
            start_result=False, error=OSError("port already in use")
        )
        tracker = Tracker(
            None,
            ui=ConsoleUI(plain=True),
            write_csv=False,
            dashboard=dashboard,
        )

        tracker.start_services()
        self.assertEqual(dashboard.starts, 1)
        tracker.shutdown()
        self.assertEqual(dashboard.stops, 1)


if __name__ == "__main__":
    unittest.main()
