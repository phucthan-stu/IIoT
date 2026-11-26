"""Offline DHT schema, sensor freshness, and additive model-link tests."""
import copy
import json
import unittest
from datetime import datetime, timezone

import hardware_model as motor
import environment_model as env_model
from environment_tracker import EnvironmentTracker, validate_combined
from test_hardware_basyx import message


def reading(index=1, attempt=1, valid=True, uptime=2500, read_at=2500, now=100):
    result = message(index=index, now=now, run_id="a" * 32)
    result["payload"]["telemetry"]["uptime_ms"] = uptime
    result["payload"]["telemetry"]["environment"] = dict(
        attempt=attempt, valid=valid, read_uptime_ms=read_at,
        temperature_c=31.9 if valid else None, humidity_pct=72.0 if valid else None)
    return result


class EnvironmentTests(unittest.TestCase):
    def test_valid_and_invalid_measurement_schema(self):
        for valid in (True, False):
            t = reading(valid=valid)["payload"]["telemetry"]
            self.assertEqual(validate_combined(json.dumps(t).encode()), t)
        for value in (True, float("nan"), 99):
            t = reading()["payload"]["telemetry"]
            t["environment"]["temperature_c"] = value
            with self.assertRaises(ValueError):
                validate_combined(json.dumps(t).encode())
        t = reading(valid=False)["payload"]["telemetry"]
        t["environment"]["temperature_c"] = 0
        with self.assertRaises(ValueError):
            validate_combined(json.dumps(t).encode())

    def test_motor_messages_do_not_refresh_same_sensor_reading(self):
        tracker = EnvironmentTracker()
        tracker.accept(reading(), now=100, mono=0)
        original = tracker.snapshot()["last_received_at"]
        for i in range(1, 14):
            tracker.accept(reading(i + 1, uptime=2500 + i * 500, now=100 + i * .5),
                           now=100 + i * .5, mono=i * .5)
        state = tracker.snapshot()
        self.assertEqual(state["freshness"], "stale")
        self.assertEqual(state["last_received_at"], original)
        self.assertEqual(state["valid_readings"], 1)
        self.assertEqual(tracker.transport.snapshot()["freshness"], "fresh")
        tracker.accept(reading(15, 2, uptime=9500, read_at=9500, now=107), now=107, mono=7)
        self.assertEqual(tracker.snapshot()["freshness"], "fresh")

    def test_error_retains_previous_measurement_without_zero(self):
        tracker = EnvironmentTracker()
        tracker.accept(reading(), now=100, mono=0)
        original = tracker.snapshot()["last_received_at"]
        tracker.accept(reading(2, 2, False, 5000, 5000, 102.5), now=102.5, mono=2.5)
        values = env_model.values_for(tracker.snapshot())
        self.assertEqual(values["AmbientTemperatureC"], 31.9)
        self.assertEqual(values["RelativeHumidityPct"], 72)
        self.assertEqual(values["Freshness"], "error")
        self.assertFalse(values["LatestAttemptValid"])
        self.assertEqual(values["LastReceivedAt"], original)
        self.assertEqual(values["Sequence"], 2)
        self.assertEqual(values["LastValidAttempt"], 1)

    def test_waiting_and_transport_stop(self):
        tracker = EnvironmentTracker()
        tracker.accept(reading(1, 0, False, 500, 0), now=100, mono=0)
        self.assertFalse(env_model.values_for(tracker.snapshot())["HasSample"])
        self.assertEqual(tracker.snapshot()["freshness"], "waiting")
        tracker.tick(mono=2)
        self.assertEqual(tracker.snapshot()["freshness"], "stale")

    def test_same_attempt_cannot_change_values(self):
        tracker = EnvironmentTracker()
        tracker.accept(reading(), now=100, mono=0)
        bad = reading(2, uptime=3000, now=100.5)
        bad["payload"]["telemetry"]["environment"]["temperature_c"] = 32
        with self.assertRaises(ValueError):
            tracker.accept(bad, now=100.5, mono=.5)
        self.assertEqual(tracker.snapshot()["accepted"], 1)

    def test_stale_age_includes_source_and_bridge_age(self):
        tracker = EnvironmentTracker()
        tracker.accept(reading(uptime=7500), now=101.5, mono=0)
        self.assertEqual(tracker.snapshot()["freshness"], "stale")

    def test_additive_bootstrap_and_idempotency(self):
        class Evidence:
            def save(self, *args):
                pass
        class FakeAPI:
            evidence = Evidence()
            def __init__(self):
                self.shell = motor.models()["shells"]
                self.submodel = None
                self.posts = 0
            def get(self, path):
                return copy.deepcopy(self.shell if path == motor.SHELL_PATH else self.submodel)
            def request(self, method, path, payload=None):
                if method == "GET":
                    return (404, None) if self.submodel is None else (200, copy.deepcopy(self.submodel))
                self.posts += 1
                if path == "/submodels":
                    self.submodel = copy.deepcopy(payload)
                elif path == motor.SHELL_PATH + "/submodel-refs":
                    self.shell["submodels"].append(copy.deepcopy(payload))
                else:
                    raise AssertionError("Unexpected mutation")
                return 201, None
        api = FakeAPI()
        env_model.bootstrap(api)
        env_model.bootstrap(api)
        self.assertEqual(api.posts, 2)
        motor.verify_shell(api.shell)
        self.assertEqual(len(api.shell["submodels"]), 2)
        self.assertNotEqual(env_model.SUBMODEL_ID, motor.SUBMODEL_ID)


if __name__ == "__main__":
    unittest.main()
