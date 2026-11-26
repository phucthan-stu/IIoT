"""Offline tests only: no Serial, MQTT broker, or BaSyx server is opened."""
import copy
import json
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timezone

import basyx_c
import hardware_model as model
from hardware_basyx_check import HardwareAdapter, Tracker
from serial_mqtt_check import envelope


def message(index=1, now=None, run_id=None):
    now = time.time() if now is None else now
    telemetry = dict(sequence=index, uptime_ms=index * 500, controller_state="idle",
                     direction="none", commanded_steps=0, remaining_steps=0,
                     coils_energized=False, reason="boot")
    return dict(payload=envelope(telemetry, datetime.fromtimestamp(now, timezone.utc).isoformat(),
                                 run_id or uuid.uuid4().hex, index), qos=1, retain=False)


class FakeAPI:
    def __init__(self):
        self.values = None
        self.block_fresh = threading.Event()
        self.entered = threading.Event()

    def request(self, method, path, values):
        assert method == "PATCH" and path == model.SM_PATH + "/$value"
        if values["Freshness"] == "fresh":
            self.entered.set()
            if not self.block_fresh.wait(5):
                raise TimeoutError("test did not release fake HTTP")
        self.values = copy.deepcopy(values)
        return 204, None

    def get(self, path):
        assert path == model.SM_PATH
        body = model.models()["submodels"]
        for prop in body["submodelElements"]:
            value = self.values[prop["idShort"]]
            prop["value"] = str(value).lower() if isinstance(value, bool) else str(value)
        return body


class HardwareTests(unittest.TestCase):
    def test_mapping_and_separate_identity(self):
        tracker = Tracker()
        tracker.accept(message())
        values = model.values_for(tracker.snapshot())
        self.assertEqual(set(values), set(model.FIELDS))
        self.assertEqual(values["Direction"], "none")
        self.assertIs(values["CoilsEnergized"], False)
        self.assertNotIn("SetpointSpeedRPM", values)
        self.assertNotIn("SampledAt", values)
        self.assertNotEqual(model.SHELL_ID, basyx_c.SHELL_ID)
        self.assertNotEqual(model.SUBMODEL_ID, basyx_c.SUBMODEL_ID)
        self.assertEqual(model.ASSET_ID, basyx_c.ASSET_ID)

    def test_rejected_samples_do_not_refresh(self):
        tracker = Tracker()
        original = message(now=100)
        tracker.accept(original, now=100, mono=0)
        last = tracker.snapshot()["last_received_at"]
        bad_messages = [copy.deepcopy(original) for _ in range(5)]
        bad_messages[1]["retain"] = True
        bad_messages[2]["payload"]["source_kind"] = "simulator"
        bad_messages[3]["payload"]["telemetry"]["remaining_steps"] = 900
        bad_messages[4]["payload"]["schema_version"] = True
        for bad in bad_messages:
            with self.assertRaises(ValueError):
                tracker.accept(bad, now=100, mono=1)
        self.assertTrue(tracker.tick(mono=2))
        self.assertEqual(tracker.snapshot()["last_received_at"], last)
        next_message = message(2, now=101, run_id=original["payload"]["bridge_run_id"])
        tracker.accept(next_message, now=101, mono=3)
        self.assertEqual(tracker.snapshot()["freshness"], "fresh")

    def test_timestamp_limits(self):
        for age in (3, -1):
            with self.assertRaises(ValueError):
                Tracker().accept(message(now=100-age), now=100, mono=0)

    def test_http_cannot_target_old_model(self):
        with tempfile.TemporaryDirectory() as folder:
            api = model.API(model.Evidence(folder))
            with self.assertRaises(ValueError):
                api.request("PATCH", basyx_c.SM_PATH + "/$value", {})
            with self.assertRaises(ValueError):
                api.request("POST", "/shells", basyx_c.models()["shells"])

    def test_watchdog_runs_while_http_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            api = FakeAPI()
            adapter = HardwareAdapter(model.Evidence(folder), api)
            adapter.accept(message())
            adapter.start()
            try:
                self.assertTrue(api.entered.wait(1))
                deadline = time.monotonic() + 3
                while adapter.tracker.snapshot()["freshness"] != "stale" and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(adapter.tracker.snapshot()["freshness"], "stale")
                self.assertFalse(api.block_fresh.is_set())
                api.block_fresh.set()
                self.assertTrue(adapter.wait_for_final_stale(timeout=2))
                self.assertEqual(api.values["Freshness"], "stale")
                self.assertEqual(api.values["LastReceivedAt"], adapter.tracker.snapshot()["last_received_at"])
            finally:
                api.block_fresh.set()
                adapter.close()


if __name__ == "__main__":
    unittest.main()
