"""DHT attempt identity and sensor age are independent of motor message rate."""
import copy
import json
import math
import threading
import time

from serial_logger import unique_keys, validate_line
from hardware_basyx_check import Tracker
from freshness import utc_now, timestamp

SENSOR_STALE_SECONDS = 6.0


def validate_combined(raw):
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(data, dict) or "environment" not in data:
        raise ValueError("Expected combined motor/environment telemetry")
    motor = dict(data)
    env = motor.pop("environment")
    validate_line(json.dumps(motor).encode())
    keys = {"attempt", "valid", "read_uptime_ms", "temperature_c", "humidity_pct"}
    if not isinstance(env, dict) or set(env) != keys:
        raise ValueError("Invalid environment fields")
    for key in ("attempt", "read_uptime_ms"):
        if type(env[key]) is not int or not 0 <= env[key] < 2**32:
            raise ValueError("Invalid environment integer: " + key)
    if type(env["valid"]) is not bool:
        raise ValueError("Invalid DHT valid flag")
    if env["attempt"] == 0 and (env["valid"] or env["read_uptime_ms"] != 0):
        raise ValueError("Uninitialized DHT must not contain a successful reading")
    if env["valid"]:
        for key, low, high in (("temperature_c", 0, 50), ("humidity_pct", 0, 100)):
            value = env[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError("Invalid environmental value: " + key)
    elif env["temperature_c"] is not None or env["humidity_pct"] is not None:
        raise ValueError("Failed DHT attempt must carry null values")
    if env["attempt"] and (data["uptime_ms"] - env["read_uptime_ms"]) % 2**32 >= 2**31:
        raise ValueError("DHT reading uptime is ahead of telemetry")
    return data


class EnvironmentTracker:
    def __init__(self):
        self.transport = Tracker(validator=validate_combined)
        self.lock = threading.RLock()
        self.current = None
        self.good = None
        self.good_mono = None
        self.good_received_at = None
        self.quality = "waiting"
        self.valid_readings = 0
        self.failed_readings = 0

    def accept(self, observed, now=None, mono=None):
        mono = time.monotonic() if mono is None else mono
        sample = observed["payload"]
        validate_combined(json.dumps(sample["telemetry"]).encode())
        env = sample["telemetry"]["environment"]
        with self.lock:
            is_new = self.current is None or env["attempt"] != self.current["attempt"]
            if self.current is not None:
                if not is_new and env != self.current:
                    raise ValueError("DHT values changed without a new attempt")
                if is_new and not 0 < (env["attempt"] - self.current["attempt"]) % 2**32 < 2**31:
                    raise ValueError("DHT attempt moved backwards")
            self.transport.accept(observed, now=now, mono=mono)
            if is_new:
                self.current = copy.deepcopy(env)
                if env["valid"]:
                    age = (sample["telemetry"]["uptime_ms"] - env["read_uptime_ms"]) % 2**32 / 1000
                    self.good = copy.deepcopy(env)
                    wall_now = time.time() if now is None else now
                    bridge_age = max(0, wall_now - timestamp(sample["pc_received_at"]))
                    self.good_mono = mono - age - bridge_age
                    self.good_received_at = utc_now()
                    self.valid_readings += 1
                elif env["attempt"]:
                    self.failed_readings += 1
            self._quality(mono)

    def _quality(self, mono):
        if self.transport.snapshot()["freshness"] == "stale":
            self.quality = "stale"
        elif self.current is None or self.current["attempt"] == 0:
            self.quality = "waiting"
        elif not self.current["valid"]:
            self.quality = "error"
        elif self.good_mono is None or mono - self.good_mono >= SENSOR_STALE_SECONDS:
            self.quality = "stale"
        else:
            self.quality = "fresh"

    def tick(self, mono=None):
        mono = time.monotonic() if mono is None else mono
        with self.lock:
            before = self.quality
            self.transport.tick(mono=mono)
            self._quality(mono)
            return self.quality != before

    def snapshot(self):
        with self.lock:
            state = self.transport.snapshot()
            state.update(current=copy.deepcopy(self.current), good=copy.deepcopy(self.good),
                         last_received_at=self.good_received_at, freshness=self.quality,
                         valid_readings=self.valid_readings, failed_readings=self.failed_readings)
            return state
