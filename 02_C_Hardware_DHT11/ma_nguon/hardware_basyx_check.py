"""Bounded ESP32 -> MQTT subscriber -> dedicated hardware AAS check."""
import argparse
import copy
import json
import msvcrt
import threading
import time
import uuid
import hardware_model as default_model

from freshness import timestamp, utc_now
from serial_logger import validate_line
from serial_mqtt_check import ROOT, run as transport_run
from hardware_model import (API, SM_PATH, STALE_SECONDS, SHELL_ID, SUBMODEL_ID,
                            bootstrap, new_evidence, values_for, verify_values)


class Tracker:
    def __init__(self, validator=validate_line):
        self.validator = validator
        self.lock = threading.Lock()
        self.sample = None
        self.last_received_at = None
        self.last_mono = None
        self.freshness = "waiting"
        self.accepted = 0

    def accept(self, message, now=None, mono=None):
        now = time.time() if now is None else now
        mono = time.monotonic() if mono is None else mono
        if message.get("retain") or message.get("qos") != 1:
            raise ValueError("retained_or_wrong_qos")
        sample = message["payload"]
        keys = {"schema_version", "source_kind", "bridge_run_id", "bridge_index",
                "pc_received_at", "telemetry"}
        if not isinstance(sample, dict) or set(sample) != keys:
            raise ValueError("hardware_envelope_fields")
        if type(sample["schema_version"]) is not int or sample["schema_version"] != 1:
            raise ValueError("hardware_schema_version")
        if sample["source_kind"] != "esp32_serial":
            raise ValueError("hardware_source_kind")
        if not isinstance(sample["bridge_run_id"], str):
            raise ValueError("bridge_run_id")
        uuid.UUID(sample["bridge_run_id"])
        if type(sample["bridge_index"]) is not int or sample["bridge_index"] < 1:
            raise ValueError("bridge_index")
        telemetry = self.validator(json.dumps(sample["telemetry"]).encode())
        received = timestamp(sample["pc_received_at"])
        if now - received > 2 or received - now > .5:
            raise ValueError("serial_receipt_timestamp_expired_or_future")
        with self.lock:
            if self.sample:
                old = self.sample
                if sample["bridge_run_id"] != old["bridge_run_id"]:
                    raise ValueError("unexpected_bridge_run_in_bounded_check")
                if sample["bridge_index"] <= old["bridge_index"]:
                    raise ValueError("duplicate_or_old_bridge_index")
                delta = (telemetry["sequence"] - old["telemetry"]["sequence"]) % 2**32
                if not 0 < delta < 2**31:
                    raise ValueError("duplicate_or_old_source_sequence")
                if received <= timestamp(old["pc_received_at"]):
                    raise ValueError("serial_receipt_time_not_increasing")
            self.sample = copy.deepcopy(sample)
            self.last_received_at = utc_now()
            self.last_mono = mono
            self.freshness = "fresh"
            self.accepted += 1

    def tick(self, mono=None):
        mono = time.monotonic() if mono is None else mono
        with self.lock:
            if self.freshness == "fresh" and mono - self.last_mono >= STALE_SECONDS:
                self.freshness = "stale"
                return True
            return False

    def snapshot(self):
        with self.lock:
            return dict(sample=copy.deepcopy(self.sample), last_received_at=self.last_received_at,
                        freshness=self.freshness, accepted=self.accepted)


class HardwareAdapter:
    def __init__(self, evidence, api, tracker=None, model=default_model):
        self.model = model
        self.evidence, self.api = evidence, api
        self.tracker = tracker if tracker is not None else Tracker()
        self.stop = threading.Event()
        self.threads = []
        self.transport_evidence = None
        self.verified = []
        self.errors = []
        self.results_lock = threading.Lock()

    def accept(self, observed):
        # Called only with the payload returned by the MQTT subscriber.
        try:
            self.tracker.accept(observed)
        except Exception as exc:
            self.evidence.event("receiver.jsonl", dict(kind="rejected", error=repr(exc), observed=observed))
            raise
        self.evidence.event("receiver.jsonl", dict(kind="accepted", observed=observed,
                                                  state=self.tracker.snapshot()))

    def start(self):
        for target in (self.watch, self.write_latest):
            thread = threading.Thread(target=target, daemon=True)
            self.threads.append(thread)
            thread.start()

    def watch(self):
        while not self.stop.wait(.05):
            if self.tracker.tick():
                state = self.tracker.snapshot()
                self.evidence.event("receiver.jsonl", dict(kind="stale", state=state))
                print("MQTT DATA STALE", flush=True)

    def write_latest(self):
        last = None
        while not self.stop.is_set():
            expected = self.model.values_for(self.tracker.snapshot())
            if expected == last:
                self.stop.wait(.05)
                continue
            began = time.monotonic()
            try:
                status, _ = self.api.request("PATCH", self.model.SM_PATH + "/$value", expected)
                if status not in (200, 204):
                    raise RuntimeError("Hardware PATCH failed: " + str(status))
                observed = self.model.verify_values(self.api.get(self.model.SM_PATH), expected)
                last = expected
                record = dict(kind="verified", expected=expected, observed=observed,
                              started_mono=began, completed_mono=time.monotonic(),
                              receiver_now=self.tracker.snapshot()["freshness"])
                self.evidence.event("writes.jsonl", record)
                with self.results_lock:
                    self.verified.append(record)
                print(f"AAS VERIFIED seq={expected['Sequence']} freshness={expected['Freshness']}", flush=True)
            except Exception as exc:
                error = repr(exc)
                with self.results_lock:
                    self.errors.append(error)
                self.evidence.event("writes.jsonl", dict(kind="write_error", error=error,
                                                        expected=expected, started_mono=began))
                print("AAS WRITE ERROR: " + error, flush=True)
                self.stop.wait(.5)

    def wait_for_final_stale(self, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.tracker.snapshot()
            if state["sample"] and state["freshness"] == "stale":
                expected = self.model.values_for(state)
                with self.results_lock:
                    if any(r["expected"] == expected for r in self.verified):
                        return True
            time.sleep(.05)
        return False

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=12)
            if thread.is_alive():
                raise RuntimeError("Hardware adapter thread did not stop")


def run(args):
    evidence = new_evidence("hardware-basyx")
    api = API(evidence)
    adapter = HardwareAdapter(evidence, api)
    control = None
    result = dict(passed=False, shell_id=SHELL_ID, submodel_id=SUBMODEL_ID,
                  hardware_transport=False, final_stale_verified=False)
    try:
        # Preflight checks both new hardware IDs; never overwrites existing metadata.
        bootstrap(api)
        adapter.start()
        if getattr(args, "motion", False):
            from motion_scenario import MotionScenario
            control = MotionScenario(evidence)
            print("MOTION ENABLED: automatic forward, partial stop, reverse", flush=True)
        result["hardware_transport"] = transport_run(args, observer=adapter, control=control) == 0
        print("Capture stopped; waiting for MQTT stale and BaSyx readback...", flush=True)
        result["final_stale_verified"] = adapter.wait_for_final_stale()
    except KeyboardInterrupt:
        result["error"] = "stopped_by_user"
    except Exception as exc:
        result["error"] = repr(exc)
    finally:
        try:
            adapter.close()
        except Exception as exc:
            result["close_error"] = repr(exc)
        state = adapter.tracker.snapshot()
        fresh = [r for r in adapter.verified if r["expected"]["Freshness"] == "fresh"]
        result.update(accepted=state["accepted"], fresh_readbacks=len(fresh),
                      write_errors=adapter.errors, final_state=state,
                      transport_evidence=adapter.transport_evidence)
        result["passed"] = (result["hardware_transport"] and result["final_stale_verified"]
                            and len(fresh) >= 3 and state["accepted"] >= args.samples
                            and not adapter.errors and "error" not in result
                            and "close_error" not in result)
        if control is not None:
            from motion_scenario import aas_motion_coverage
            result["motion"] = control.result()
            result["aas_motion"] = aas_motion_coverage(adapter.verified, control)
            result["passed"] = result["passed"] and control.completed and result["aas_motion"]["passed"]
        evidence.save("verification.json", result)
        print("HARDWARE AAS " + ("PASS" if result["passed"] else "NOT PASSED"), flush=True)
        if control is not None:
            print("MOTION AAS " + ("PASS" if result["passed"] else "NOT PASSED"), flush=True)
        print("EVIDENCE: " + str(evidence.output), flush=True)
        if "error" in result:
            print("ERROR: " + result["error"], flush=True)
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--samples", type=int)
    parser.add_argument("--motion", action="store_true", help="Send automatic f/s/r motor commands")
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()
    if args.samples is None:
        args.samples = 40 if args.motion else 20
    if not 3 <= args.samples <= 50 or not 5 <= args.duration <= 60:
        parser.error("samples must be 3..50; duration must be 5..60")
    with (ROOT / "hardware-basyx.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            parser.exit(1, "Hardware AAS check already running\n")
        try:
            return run(args)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    raise SystemExit(main())
