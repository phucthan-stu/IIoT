"""Offline motion-command checks. No real port is opened."""
import unittest
from motion_scenario import MotionScenario, aas_motion_coverage


class Evidence:
    def event(self, *args):
        pass


class Device:
    def __init__(self):
        self.commands = []

    def write(self, data):
        self.commands.append(data)
        return len(data)


def sample(state="idle", direction="none", steps=100, remaining=0,
           coils=False, reason="boot"):
    return dict(controller_state=state, direction=direction, commanded_steps=steps,
                remaining_steps=remaining, coils_energized=coils, reason=reason)


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.control = MotionScenario(Evidence(), clock=lambda: self.now)
        self.device = Device()

    def test_complete_sequence_and_no_extra_stop(self):
        c = self.control
        c.start()
        c.observe(sample())
        self.now = 1.9
        c.observe(sample())
        c.tick(self.device)
        self.assertEqual(self.device.commands, [])
        self.now = 2
        c.tick(self.device)
        self.now = 2.5
        c.observe(sample("stepping", "forward", 220, 392, True, "start_command"))
        self.now = 3.25
        c.tick(self.device)
        self.now = 3.5
        c.observe(sample("idle", "forward", 410, 0, False, "stop_command"))
        self.now = 6.4
        c.observe(sample("idle", "forward", 410, 0, False, "stop_command"))
        self.now = 6.5
        c.tick(self.device)
        self.now = 7
        c.observe(sample("stepping", "reverse", 530, 392, True, "start_command"))
        self.now = 9
        c.observe(sample("idle", "reverse", 922, 0, False, "completed"))
        c.finish()
        c.close(self.device)
        self.assertEqual(self.device.commands, [b"f", b"s", b"r"])
        self.assertTrue(c.completed)

    def test_silence_stops_on_exit(self):
        c = self.control
        c.observe(sample())
        self.now = 1.9
        c.observe(sample())
        self.now = 2
        c.tick(self.device)
        self.now = 4
        with self.assertRaises(TimeoutError):
            c.tick(self.device)
        c.close(self.device)
        self.assertEqual(self.device.commands, [b"f", b"s"])
        self.assertFalse(c.completed)

    def test_full_forward_is_not_partial_stop(self):
        c = self.control
        c.baseline = 100
        c.stage = "stopping"
        c.forward_seen = True
        with self.assertRaises(RuntimeError):
            c.observe(sample("idle", "forward", 612, 0, False, "stop_command"))

    def test_aas_coverage_requires_order_and_all_phases(self):
        c = self.control
        c.baseline, c.stopped_steps = 100, 410
        def record(state, direction, steps, remain, coils, reason):
            return {"expected":dict(Freshness="fresh", HasSample=True, ControllerState=state,
                                    Direction=direction, CommandedSteps=steps, RemainingSteps=remain,
                                    CoilsEnergized=coils, Reason=reason)}
        rows = [record("stepping", "forward", 220, 392, True, "start_command"),
                record("idle", "forward", 410, 0, False, "stop_command"),
                record("stepping", "reverse", 530, 392, True, "start_command"),
                record("idle", "reverse", 922, 0, False, "completed")]
        self.assertTrue(aas_motion_coverage(rows, c)["passed"])
        self.assertFalse(aas_motion_coverage(rows[:2] + rows[3:], c)["passed"])
        self.assertFalse(aas_motion_coverage(list(reversed(rows)), c)["passed"])


if __name__ == "__main__":
    unittest.main()
