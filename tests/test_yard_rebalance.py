import unittest
from datetime import datetime, timezone
from railforge.workflows.control import RailForgeWorkflowControl
from railforge.workflows.features import RailForgeFeatureService
from railforge.yard.hump import Cut, Track, classify
from railforge.yard.switching import Route, Switch

U = timezone.utc
AT = datetime(2026, 1, 1, 8, tzinfo=U)
ROUTE = Route('R1', {'S1': 'normal'})
SWITCHES = {'S1': Switch('S1', 'normal')}


def service():
    control = RailForgeWorkflowControl()
    return RailForgeFeatureService(control), control


class ClassifyTests(unittest.TestCase):
    def test_cumulative_capacity_across_batches(self):
        tracks = [Track('T1', 10, frozenset({'D'}), frozenset()),
                  Track('T2', 10, frozenset({'D'}), frozenset())]
        batch1 = [Cut('B1C1', 'D', 6)]
        batch2 = [Cut('B2C1', 'D', 6)]
        plan = classify(batch1 + batch2, tracks, {})
        self.assertEqual(plan, {'B1C1': 'T1', 'B2C1': 'T2'})

    def test_cumulative_overflow_rejected(self):
        tracks = [Track('T1', 10, frozenset({'D'}), frozenset())]
        cuts = [Cut('B1C1', 'D', 6), Cut('B2C1', 'D', 6)]
        with self.assertRaises(ValueError):
            classify(cuts, tracks, {})

    def test_initial_occupancy_counts(self):
        tracks = [Track('T1', 10, frozenset({'D'}), frozenset())]
        with self.assertRaises(ValueError):
            classify([Cut('B1C1', 'D', 6), Cut('B2C1', 'D', 3)], tracks, {'T1': 2})

    def test_hazmat_incompatibility_splits_tracks(self):
        tracks = [Track('T1', 20, frozenset({'D'}), frozenset()),
                  Track('T2', 20, frozenset({'D'}), frozenset())]
        cuts = [Cut('H1', 'D', 5, 'G1'), Cut('H2', 'D', 5, 'G2')]
        plan = classify(cuts, tracks, {}, {frozenset({'G1', 'G2'})})
        self.assertEqual(plan, {'H1': 'T1', 'H2': 'T2'})

    def test_hazmat_incompatibility_without_escape_fails(self):
        tracks = [Track('T1', 20, frozenset({'D'}), frozenset())]
        cuts = [Cut('H1', 'D', 5, 'G1'), Cut('H2', 'D', 5, 'G2')]
        with self.assertRaises(ValueError):
            classify(cuts, tracks, {}, {frozenset({'G1', 'G2'})})

    def test_track_forbidden_hazard_still_enforced(self):
        tracks = [Track('T1', 20, frozenset({'D'}), frozenset({'G1'}))]
        with self.assertRaises(ValueError):
            classify([Cut('H1', 'D', 5, 'G1')], tracks, {})


class RebalanceTests(unittest.TestCase):
    def test_success_commits_one_atomic_event(self):
        svc, control = service()
        tracks = [Track('T1', 10, frozenset({'D'}), frozenset())]
        receipt = svc.rebalance_yard('OP1', [Cut('B1C1', 'D', 6)], tracks, {},
                                     ROUTE, SWITCHES, 'hump-1', AT)
        self.assertEqual(receipt.result['assignment'], {'B1C1': 'T1'})
        self.assertEqual(receipt.event.payload['status'], 'committed')
        self.assertEqual(receipt.event.payload['control_path'], 'atomic')
        self.assertEqual(control.stream.head, 1)
        self.assertTrue(control.stream.verify_chain())
        self.assertEqual(control.store.read('OP1').version, 1)
        self.assertTrue(control.trail.verify())

    def test_failed_batch_rolls_back_whole_rebalance(self):
        svc, control = service()
        tracks = [Track('T1', 10, frozenset({'D'}), frozenset())]
        cuts = [Cut('B1C1', 'D', 6), Cut('B2C1', 'D', 6)]
        with self.assertRaises(ValueError):
            svc.rebalance_yard('OP2', cuts, tracks, {}, ROUTE, SWITCHES, 'hump-1', AT)
        self.assertEqual(control.stream.head, 0)
        self.assertEqual(control.store.snapshot(), {})
        self.assertEqual(control.trail._rows, [])

    def test_hazmat_conflict_rolls_back_whole_rebalance(self):
        svc, control = service()
        tracks = [Track('T1', 20, frozenset({'D'}), frozenset())]
        cuts = [Cut('H1', 'D', 5, 'G1'), Cut('H2', 'D', 5, 'G2')]
        with self.assertRaises(ValueError):
            svc.rebalance_yard('OP3', cuts, tracks, {}, ROUTE, SWITCHES, 'hump-1', AT,
                               hazard_conflicts={frozenset({'G1', 'G2'})})
        self.assertEqual(control.stream.head, 0)
        self.assertEqual(control.store.snapshot(), {})
        self.assertEqual(control.trail._rows, [])

    def test_locked_route_rolls_back_whole_rebalance(self):
        svc, control = service()
        tracks = [Track('T1', 10, frozenset({'D'}), frozenset())]
        locked = {'S1': Switch('S1', 'normal', locked_by='other-owner')}
        with self.assertRaises(ValueError):
            svc.rebalance_yard('OP4', [Cut('B1C1', 'D', 6)], tracks, {},
                               ROUTE, locked, 'hump-1', AT)
        self.assertEqual(control.stream.head, 0)
        self.assertEqual(control.store.snapshot(), {})
        self.assertEqual(control.trail._rows, [])

    def test_rebalance_locks_route_for_owner(self):
        svc, control = service()
        tracks = [Track('T1', 10, frozenset({'D'}), frozenset())]
        receipt = svc.rebalance_yard('OP5', [Cut('B1C1', 'D', 6)], tracks, {},
                                     ROUTE, SWITCHES, 'hump-1', AT)
        self.assertEqual(receipt.result['switches']['S1'].locked_by, 'hump-1')


class AtomicTopicTests(unittest.TestCase):
    def test_yard_classified_commits_atomically(self):
        control = RailForgeWorkflowControl()
        tracks = [Track('T1', 10, frozenset({'D'}), frozenset())]
        receipt = control.classify_yard('OP6', [Cut('B1C1', 'D', 6)], tracks, {}, 'YARD-1', AT)
        self.assertEqual(receipt.event.payload['status'], 'committed')
        self.assertEqual(receipt.event.payload['control_path'], 'atomic')
        self.assertTrue(control.stream.verify_chain())
        self.assertTrue(control.trail.verify())


if __name__ == '__main__':
    unittest.main()
