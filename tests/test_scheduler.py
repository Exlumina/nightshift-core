import copy
import unittest
from nightshift_core.scheduler import schedule


def issue(key, **fields):
    base = dict(id=key, title=key, status='Todo', priority=2,
                authorized=True, owner_available=True, estimate_seconds=30,
                verify_seconds=5, stop_seconds=5, targets=[key], resource_class='light')
    base.update(fields)
    return base


class SchedulerTests(unittest.TestCase):
    def run_schedule(self, rows, **kwargs):
        options = dict(remaining_seconds=3600, capabilities=set(), now=1000)
        options.update(kwargs)
        return schedule(rows, **options)



    def test_priority_beats_tiny_low_jobs_and_preserves_input(self):
        rows = [issue('tiny', priority=4, estimate_seconds=1), issue('high', estimate_seconds=120)]
        before = copy.deepcopy(rows)
        result = self.run_schedule(rows, resources={'max_parallel': 1})
        self.assertEqual([r['id'] for r in result['ready']], ['high'])
        self.assertEqual(rows, before)

    def test_blocked_urgent_does_not_freeze_queue(self):
        result = self.run_schedule([issue('urgent', priority=1, dependencies=['missing']), issue('high')])
        self.assertEqual([r['id'] for r in result['ready']], ['high'])
        self.assertIn({'id': 'urgent', 'reason': 'missing_dependency:missing'}, result['excluded'])

    def test_dependency_inherits_priority_but_parent_waits(self):
        rows = [issue('release', priority=1, dependencies=['fix']), issue('other', priority=2), issue('fix', priority=3)]
        result = self.run_schedule(rows, resources={'max_parallel': 1})
        self.assertEqual([r['id'] for r in result['ready']], ['fix'])
        self.assertEqual(result['effective_priority']['fix'], 1)
        self.assertEqual(rows[2]['priority'], 3)

    def test_transitive_inheritance_and_completed_dependency(self):
        rows = [issue('a', priority=1, dependencies=['b']), issue('b', priority=3, dependencies=['c']),
                issue('c', priority=4, dependencies=['done']), issue('done', status='Done')]
        result = self.run_schedule(rows)
        self.assertEqual([r['id'] for r in result['ready']], ['c'])
        self.assertEqual(result['effective_priority']['c'], 1)

    def test_cycle_is_reported_and_unrelated_work_runs(self):
        result = self.run_schedule([issue('a', dependencies=['b']), issue('b', dependencies=['a']), issue('c')])
        self.assertEqual([r['id'] for r in result['ready']], ['c'])
        self.assertTrue(all(x in result['excluded'] for x in [dict(id='a', reason='dependency_cycle'), dict(id='b', reason='dependency_cycle')]))

    def test_unauthorized_owner_missing_capability_and_triage_fail_closed(self):
        rows = [issue('unauth', authorized=False), issue('owner', owner_available=False),
                issue('cap', capabilities=['render']), issue('triage', priority=0)]
        result = self.run_schedule(rows)
        self.assertFalse(result['ready'])
        reasons = {r['id']: r['reason'] for r in result['excluded']}
        self.assertEqual(reasons, {'unauth':'not_authorized','owner':'owner_unavailable','cap':'missing_capability:render','triage':'needs_priority_triage'})
        self.assertFalse(self.run_schedule([{'id':'raw', 'status':'Todo', 'priority':2}])['ready'])

    def test_time_includes_verification_and_stop(self):
        result = self.run_schedule([issue('fits', estimate_seconds=40), issue('too-long', estimate_seconds=41)], remaining_seconds=50)
        self.assertEqual([r['id'] for r in result['ready']], ['fits'])
        self.assertIn({'id':'too-long','reason':'insufficient_time'}, result['excluded'])

    def test_backlog_only_when_enabled_and_ready_queue_empty(self):
        rows = [issue('backlog', status='Backlog', priority=1), issue('ready', priority=3)]
        result = self.run_schedule(rows, allow_backlog=True)
        self.assertEqual([r['id'] for r in result['ready']], ['ready'])
        self.assertFalse(self.run_schedule(rows[:1])['ready'])
        self.assertEqual([r['id'] for r in self.run_schedule(rows[:1], allow_backlog=True)['ready']], ['backlog'])

    def test_explicit_direction_then_near_deadline_then_priority(self):
        rows = [issue('urgent', priority=1), issue('deadline', priority=4, due_at=1200),
                issue('explicit', priority=3, explicit_order=0), issue('distant', priority=4, due_at=100000)]
        result = self.run_schedule(rows, resources={'max_parallel': 5})
        self.assertEqual([r['id'] for r in result['ranked']], ['explicit','deadline','urgent','distant'])

    def test_infeasible_future_deadline_is_not_promised(self):
        result = self.run_schedule([issue('late', due_at=1010), issue('other')])
        self.assertEqual([r['id'] for r in result['ready']], ['other'])
        self.assertIn({'id':'late','reason':'deadline_infeasible'}, result['excluded'])

    def test_heavy_serialization_targets_and_light_parallelism(self):
        rows = [issue('render', priority=1, resource_class='heavy', targets=['model']),
                issue('build', resource_class='heavy'), issue('conflict', targets=['model']), issue('docs')]
        result = self.run_schedule(rows, resources={'max_parallel': 4})
        self.assertEqual([r['id'] for r in result['ready']], ['render','docs'])
        self.assertIn({'id':'build','reason':'heavy_capacity'}, result['deferred'])
        self.assertIn({'id':'conflict','reason':'target_conflict'}, result['deferred'])

    def test_pressure_blocks_heavy_but_not_light(self):
        result = self.run_schedule([issue('heavy', resource_class='heavy'), issue('light')], resources={'memory_pressure':'critical'})
        self.assertEqual([r['id'] for r in result['ready']], ['light'])
        self.assertIn({'id':'heavy','reason':'resource_pressure'}, result['deferred'])

    def test_active_jobs_and_targets_take_capacity(self):
        rows = [issue('conflict', targets=['occupied']), issue('new')]
        result = self.run_schedule(rows, active_targets={'occupied'}, resources={'max_parallel':2,'active_jobs':1})
        self.assertEqual([r['id'] for r in result['ready']], ['new'])

    def test_memory_and_disk_budgets_accumulate_across_selected_jobs(self):
        rows = [issue('one', memory_bytes=60, disk_bytes=5), issue('two', memory_bytes=60, disk_bytes=5), issue('disk', memory_bytes=1,disk_bytes=20)]
        result = self.run_schedule(rows, resources={'max_parallel':3,'available_memory_bytes':100,'available_disk_bytes':10})
        self.assertEqual([r['id'] for r in result['ready']], ['one'])
        self.assertIn({'id':'two','reason':'memory_capacity'}, result['deferred'])
        self.assertIn({'id':'disk','reason':'disk_capacity'}, result['deferred'])

    def test_age_breaks_equal_priority_ties(self):
        result = self.run_schedule([issue('new', ready_since=900), issue('old', ready_since=200)])
        self.assertEqual([r['id'] for r in result['ranked']], ['old','new'])

    def test_invalid_numbers_dates_and_duplicate_ids_fail_closed(self):
        rows = [issue('negative', estimate_seconds=-1),issue('nan', estimate_seconds=float('nan')),
                issue('date', due_at='tomorrow'), issue('duplicate'),issue('duplicate')]
        result = self.run_schedule(rows)
        self.assertFalse(result['ready'])
        self.assertEqual(len(result['excluded']),5)

    def test_unprioritized_prerequisite_can_inherit_priority(self):
        result = self.run_schedule([issue('release',priority=1,dependencies=['fix']),issue('fix',priority=0)])
        self.assertEqual([r['id'] for r in result['ready']], ['fix'])

    def test_human_blocked_and_already_owned_are_excluded(self):
        result = self.run_schedule([issue('human',human_blocked=True),issue('active',claimed_by_other=True)])
        self.assertFalse(result['ready'])

    def test_completed_and_terminal_statuses_do_not_dispatch(self):
        rows=[issue('done',status={'name':'Done','type':'completed'}),issue('review',status='In Review'),issue('cancel',status='Canceled')]
        self.assertFalse(self.run_schedule(rows)['ready'])

    def test_prerequisite_inherits_explicit_direction_and_deadline(self):
        rows = [issue('release', explicit_order=0, due_at=1400, dependencies=['fix']),
                issue('other', priority=1, due_at=1100), issue('fix', priority=3)]
        result = self.run_schedule(rows, resources={'max_parallel': 1})
        self.assertEqual([r['id'] for r in result['ready']], ['fix'])

    def test_prerequisite_inherits_priority_from_parent_too_large_for_window(self):
        rows = [issue('release', priority=1, estimate_seconds=10000, dependencies=['fix']),
                issue('other', priority=2), issue('fix', priority=4)]
        result = self.run_schedule(rows, resources={'max_parallel': 1})
        self.assertEqual([r['id'] for r in result['ready']], ['fix'])

    def test_nested_targets_conflict_and_invalid_resource_class_fails(self):
        result = self.run_schedule([issue('nested',targets=['/project/source']), issue('bad',resource_class='unknown')],active_targets={'/project'})
        self.assertFalse(result['ready'])
        self.assertIn({'id':'nested','reason':'target_conflict'},result['deferred'])
        self.assertIn({'id':'bad','reason':'invalid_contract'},result['excluded'])

    def test_fractional_slot_configuration_raises(self):
        with self.assertRaises(ValueError):
            self.run_schedule([],resources={'max_parallel':1.5})

    def test_unhashable_invalid_issue_identity_is_excluded(self):
        result = self.run_schedule([issue([], title='invalid'), issue('valid')])
        self.assertEqual([r['id'] for r in result['ready']], ['valid'])
        self.assertIn({'id': [], 'reason': 'invalid_id'}, result['excluded'])

    def test_invalid_shift_budget_raises(self):
        with self.assertRaises(ValueError):
            self.run_schedule([],remaining_seconds=float('inf'))

    def test_explicit_human_work_is_skipped_while_render_runs(self):
        held = [
            issue('joint', title='Review publication options together'),
            issue('description', description='This needs to be done with a human.'),
            issue('decision', description='A joint decision is required before execution.'),
            issue('wait', description='Waiting for the user input.'),
            issue('presence', requires_human_presence=True),
            issue('disabled', autonomous=False),
            issue('label', labels=[{'name': 'Requires-Human'}]),
        ]
        render = issue('render', title='Render the approved image gallery', capabilities=['render'])
        result = self.run_schedule(held + [render], capabilities={'render'})
        self.assertEqual([row['id'] for row in result['ready']], ['render'])
        self.assertEqual({row['id'] for row in result['excluded']}, {row['id'] for row in held})
        self.assertEqual({row['reason'] for row in result['excluded']}, {'requires_human'})

    def test_later_human_acceptance_does_not_block_approved_autonomous_render(self):
        rows = [issue('render', description='Render approved scenes autonomously; the user reviews results after the shift. No human input required.'),
                issue('docs', description='Prepare a review checklist without human input.')]
        result = self.run_schedule(rows)
        self.assertEqual({row['id'] for row in result['ready']}, {'render', 'docs'})
