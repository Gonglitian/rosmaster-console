import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hub.battery import BatteryEstimator, percent_for  # noqa: E402


def feed(est, volts, t0, seconds, moving=False, hz=10.0):
    t = t0
    for _ in range(int(seconds * hz)):
        est.update(volts, t, moving)
        t += 1.0 / hz
    return t


class BatteryTest(unittest.TestCase):
    def test_table(self):
        self.assertEqual(percent_for(9.0), 0)
        self.assertEqual(percent_for(9.6), 0)
        self.assertAlmostEqual(percent_for(10.3), 11.0)
        self.assertAlmostEqual(percent_for(11.1), 43)
        self.assertEqual(percent_for(12.8), 100)

    def test_no_data_and_startup_zero(self):
        est = BatteryEstimator()
        self.assertEqual(est.snapshot(0)['state'], 'nodata')
        feed(est, 0.0, 0, 1)           # driver publishes 0.0 V before the first STM32 packet
        self.assertEqual(est.snapshot(1)['state'], 'nodata')

    def test_resting_estimate_rounds_to_5(self):
        est = BatteryEstimator()
        t = feed(est, 10.3, 0, 5)
        snap = est.snapshot(t)
        self.assertEqual(snap['state'], 'resting')
        self.assertEqual(snap['pct'], 10)
        self.assertEqual(snap['level'], 'warn')

    def test_driving_holds_last_resting_value(self):
        est = BatteryEstimator()
        t = feed(est, 11.1, 0, 5)
        pct = est.snapshot(t)['pct']
        t = feed(est, 10.5, t, 5, moving=True)   # sag while driving
        snap = est.snapshot(t)
        self.assertEqual(snap['state'], 'driving')
        self.assertEqual(snap['pct'], pct)

    def test_only_goes_down_but_resets_after_charge(self):
        est = BatteryEstimator()
        t = feed(est, 11.1, 0, 5)
        t = feed(est, 11.3, t, 20)               # small recovery: display does not rise
        self.assertEqual(est.snapshot(t)['pct'], 45)
        t = feed(est, 12.5, t, 40)               # charged: sustained jump restarts estimation
        self.assertGreaterEqual(est.snapshot(t)['pct'], 95)

    def test_isolated_spike_ignored(self):
        est = BatteryEstimator()
        t = feed(est, 11.1, 0, 5)
        est.update(12.6, t, False)
        t = feed(est, 11.1, t + 0.1, 1)
        self.assertEqual(est.snapshot(t)['pct'], 45)

    def test_stale_after_chassis_off(self):
        est = BatteryEstimator()
        t = feed(est, 11.1, 0, 5)
        self.assertEqual(est.snapshot(t + 5)['state'], 'nodata')
        self.assertFalse(est.below_floor(t + 5))

    def test_floor_and_stop_levels(self):
        est = BatteryEstimator()
        t = feed(est, 9.5, 0, 3)
        self.assertEqual(est.snapshot(t)['level'], 'stop')
        self.assertFalse(est.below_floor(t))
        est2 = BatteryEstimator()
        t = feed(est2, 8.9, 0, 3)
        self.assertTrue(est2.below_floor(t))

    def test_trend_needs_five_minutes(self):
        est = BatteryEstimator()
        t = feed(est, 11.1, 0, 120)
        self.assertIsNone(est.trend(t))
        self.assertIsNone(est.eta(t))

    def test_drain_rate_and_eta(self):
        est = BatteryEstimator()
        t = 0.0
        v = 11.10
        for _ in range(20 * 60):              # 20 min, losing 0.3 V per hour
            est.update(round(v, 2), t, False)
            t += 1.0
            v -= 0.3 / 3600.0
        tr = est.trend(t)
        self.assertAlmostEqual(tr['v_per_h'], -0.3, delta=0.08)
        self.assertLess(tr['pct_per_h'], 0)
        eta = est.eta(t)
        self.assertEqual(eta['status'], 'discharging')
        self.assertLess(eta['warn'], eta['critical'])
        self.assertLess(eta['critical'], eta['alarm'])

    def test_driving_points_excluded_from_trend(self):
        est = BatteryEstimator()
        t = feed(est, 11.1, 0, 400, hz=1.0)
        t = feed(est, 10.4, t, 300, moving=True, hz=1.0)   # sag while driving
        tr = est.trend(t)
        self.assertTrue(tr is None or abs(tr['v_per_h']) < 0.2)

    def test_history_and_snapshot_fields(self):
        est = BatteryEstimator()
        t = feed(est, 11.1, 0, 30)
        snap = est.snapshot(t)
        self.assertAlmostEqual(snap['cell_v'], 3.7, places=2)
        self.assertEqual(snap['pct_exact'], 43.0)
        self.assertGreater(len(est.history_points()), 3)


if __name__ == '__main__':
    unittest.main()
