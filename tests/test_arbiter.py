import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hub.arbiter import Arbiter, IDLE, MANUAL, POLICY  # noqa: E402


class FakeClock(object):
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make():
    clock = FakeClock()
    arb = Arbiter(max_linear=0.7, max_angular=1.5, max_linear_accel=1.5,
                  max_angular_accel=4.0, source_timeout=0.5, clock=clock)
    arb.tick()  # establish the tick reference time
    return arb, clock


def run(arb, clock, seconds, refresh=None, dt=0.05):
    out = arb.output
    for _ in range(int(round(seconds / dt))):
        clock.advance(dt)
        if refresh:
            refresh()
        out = arb.tick()
    return out


class ArbiterTest(unittest.TestCase):
    def test_idle_outputs_zero(self):
        arb, clock = make()
        self.assertEqual(run(arb, clock, 0.5), (0.0, 0.0, 0.0))
        self.assertEqual(arb.mode, IDLE)

    def test_manual_ramps_up_and_is_clamped(self):
        arb, clock = make()
        arb.set_manual(2.0, 0.0, 0.0)
        self.assertEqual(arb.mode, MANUAL)
        clock.advance(0.05)
        vx = arb.tick()[0]
        self.assertAlmostEqual(vx, 1.5 * 0.05)
        out = run(arb, clock, 1.0, refresh=lambda: arb.set_manual(2.0, 0.0, 0.0))
        self.assertAlmostEqual(out[0], 0.7)

    def test_linear_speed_is_clamped_as_a_vector(self):
        arb, clock = make()
        out = run(arb, clock, 1.0, refresh=lambda: arb.set_manual(0.7, 0.7, 0.0))
        self.assertLessEqual(math.hypot(out[0], out[1]), 0.7 + 1e-9)

    def test_angular_clamp(self):
        arb, clock = make()
        out = run(arb, clock, 1.0, refresh=lambda: arb.set_manual(0.0, 0.0, -9.0))
        self.assertAlmostEqual(out[2], -1.5)

    def test_silent_source_stops_immediately(self):
        arb, clock = make()
        run(arb, clock, 1.0, refresh=lambda: arb.set_manual(0.5, 0.0, 0.0))
        self.assertAlmostEqual(arb.output[0], 0.5)
        clock.advance(0.6)  # no new manual command for longer than the timeout
        self.assertEqual(arb.tick(), (0.0, 0.0, 0.0))
        self.assertEqual(arb.mode, MANUAL)  # still in charge, just not moving

    def test_slowing_down_is_not_rate_limited(self):
        arb, clock = make()
        run(arb, clock, 1.0, refresh=lambda: arb.set_manual(0.5, 0.0, 0.0))
        arb.set_manual(0.1, 0.0, 0.0)
        clock.advance(0.05)
        self.assertAlmostEqual(arb.tick()[0], 0.1)

    def test_sign_flip_goes_through_zero(self):
        arb, clock = make()
        run(arb, clock, 1.0, refresh=lambda: arb.set_manual(0.5, 0.0, 0.0))
        arb.set_manual(-0.5, 0.0, 0.0)
        clock.advance(0.05)
        self.assertEqual(arb.tick()[0], 0.0)
        clock.advance(0.05)
        self.assertAlmostEqual(arb.tick()[0], -0.075)

    def test_estop_latches_and_blocks_manual(self):
        arb, clock = make()
        run(arb, clock, 1.0, refresh=lambda: arb.set_manual(0.5, 0.0, 0.0))
        arb.trigger_estop()
        self.assertEqual(arb.output, (0.0, 0.0, 0.0))
        self.assertFalse(arb.set_manual(0.5, 0.0, 0.0))
        self.assertEqual(run(arb, clock, 0.5), (0.0, 0.0, 0.0))
        arb.release_estop()
        self.assertEqual(arb.mode, IDLE)
        self.assertTrue(arb.set_manual(0.2, 0.0, 0.0))

    def test_manual_overrides_policy_until_hand_back(self):
        arb, clock = make()
        arb.activate_policy('w1')
        arb.hand_back()
        self.assertEqual(arb.mode, POLICY)
        out = run(arb, clock, 1.0, refresh=lambda: arb.set_policy('w1', 0.3, 0.0, 0.0))
        self.assertAlmostEqual(out[0], 0.3)

        # Someone touches the joystick: manual wins at once, policy is ignored.
        def both():
            arb.set_policy('w1', 0.3, 0.0, 0.0)
            arb.set_manual(0.0, 0.2, 0.0)
        out = run(arb, clock, 1.0, refresh=both)
        self.assertEqual(arb.mode, MANUAL)
        self.assertEqual(out[0], 0.0)
        self.assertAlmostEqual(out[1], 0.2)

        # Releasing the joystick does not hand control back.
        arb.release_manual()
        out = run(arb, clock, 1.0, refresh=lambda: arb.set_policy('w1', 0.3, 0.0, 0.0))
        self.assertEqual(arb.mode, MANUAL)
        self.assertEqual(out, (0.0, 0.0, 0.0))

        arb.hand_back()
        out = run(arb, clock, 1.0, refresh=lambda: arb.set_policy('w1', 0.3, 0.0, 0.0))
        self.assertEqual(arb.mode, POLICY)
        self.assertAlmostEqual(out[0], 0.3)

    def test_inactive_worker_is_ignored(self):
        arb, clock = make()
        arb.activate_policy('w1')
        arb.hand_back()
        self.assertFalse(arb.set_policy('w2', 0.3, 0.0, 0.0))
        self.assertEqual(run(arb, clock, 0.5), (0.0, 0.0, 0.0))

    def test_estop_release_does_not_resume_policy(self):
        arb, clock = make()
        arb.activate_policy('w1')
        arb.hand_back()
        arb.trigger_estop()
        arb.release_estop()
        self.assertEqual(arb.mode, IDLE)
        self.assertEqual(run(arb, clock, 0.5, refresh=lambda: arb.set_policy('w1', 0.3, 0, 0)),
                         (0.0, 0.0, 0.0))

    def test_non_finite_input_becomes_zero(self):
        arb, clock = make()
        out = run(arb, clock, 0.5, refresh=lambda: arb.set_manual(float('nan'), 'x', float('inf')))
        self.assertEqual(out, (0.0, 0.0, 0.0))


if __name__ == '__main__':
    unittest.main()
