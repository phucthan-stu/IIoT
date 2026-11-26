"""Offline acceptance checks for combined DHT and motor operation."""
import copy
import unittest
from types import SimpleNamespace
from dht_basyx_check import combined_motion_coverage


class CombinedMotionTests(unittest.TestCase):
    def setUp(self):
        self.control = SimpleNamespace(baseline=0, stopped_steps=323, completed=True)
        def record(state, direction, steps, remaining, coils, reason, uptime):
            return {"expected": dict(Freshness="fresh", HasSample=True, ControllerState=state,
                    Direction=direction, CommandedSteps=steps, RemainingSteps=remaining,
                    CoilsEnergized=coils, Reason=reason, UptimeMs=uptime, BridgeRunId="run1")}
        self.motor = [record("stepping", "forward", 150, 362, True, "start_command", 3000),
                      record("idle", "forward", 323, 0, False, "stop_command", 4000),
                      record("stepping", "reverse", 500, 335, True, "start_command", 7500),
                      record("idle", "reverse", 835, 0, False, "completed", 9000)]
        self.env = [{"expected": dict(Freshness="fresh", HasSample=True, LatestAttemptValid=True,
                     LastValidReadUptimeMs=10000, LastValidAttempt=4, BridgeRunId="run1")}]

    def test_complete_and_new_reading_pass(self):
        self.assertTrue(combined_motion_coverage(self.motor, self.env, self.control)["passed"])

    def test_old_or_invalid_sensor_reading_fails(self):
        for key, value in (("LastValidReadUptimeMs", 8000), ("LatestAttemptValid", False),
                           ("Freshness", "stale"), ("BridgeRunId", "another-run")):
            env = copy.deepcopy(self.env)
            env[0]["expected"][key] = value
            self.assertFalse(combined_motion_coverage(self.motor, env, self.control)["passed"])

    def test_missing_motion_phase_fails(self):
        self.assertFalse(combined_motion_coverage(self.motor[1:], self.env, self.control)["passed"])


if __name__ == "__main__":
    unittest.main()
