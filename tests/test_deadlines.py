import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from nightshift_core.deadlines import normalize_due
class Deadlines(unittest.TestCase):
 def test_local_day_boundary(self):
  r=normalize_due({'dueDate':'2026-10-18'},timezone='Europe/Berlin');d=datetime.fromtimestamp(r['due_at'],ZoneInfo('Europe/Berlin'))
  self.assertEqual((d.hour,d.minute,d.second),(23,59,59));self.assertIn('date-only',r['deadline_resolution'])
 def test_explicit_timestamp_and_override(self):
  self.assertEqual(normalize_due({'dueDate':'2026-10-18T10:00:00Z'})['due_at'],1792317600)
  self.assertEqual(normalize_due({'dueDate':'bad','due_at':42})['due_at'],42)
 def test_bad_date_not_silently_ignored(self):
  with self.assertRaises(ValueError):normalize_due({'dueDate':'tomorrow'})
