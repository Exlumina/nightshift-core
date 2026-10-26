import unittest
from nightshift_core import schedule, normalize_due
from datetime import datetime, timezone

class PublicBoundary(unittest.TestCase):
    def test_no_workload_specific_pressure_bypass(self):
        row = dict(id="example", title="Example", status="Todo", priority=2,
                   authorized=True, owner_available=True, estimate_seconds=10,
                   verify_seconds=1, stop_seconds=1, targets=["synthetic"],
                   resource_class="heavy")
        result = schedule([row], remaining_seconds=60, capabilities=set(), now=1000,
                          resources={"heavy_allowed": False, "memory_pressure":"critical"})
        self.assertEqual(result["ready"], [])
        self.assertIn({"id":"example", "reason":"resource_pressure"}, result["deferred"])

    def test_utc_default_is_explicit(self):
        row = normalize_due({"dueDate":"2026-10-27"})
        due = datetime.fromtimestamp(row["due_at"], timezone.utc)
        self.assertEqual((due.hour, due.minute, due.second),(23,59,59))
