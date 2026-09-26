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


if __name__ == '__main__':
    unittest.main()
