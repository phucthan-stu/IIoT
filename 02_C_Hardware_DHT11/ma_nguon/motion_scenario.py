"""Explicit opt-in, bounded f -> s -> r scenario; telemetry is not shaft feedback."""
import time


class MotionScenario:
    def __init__(self, evidence, clock=time.monotonic):
        self.evidence = evidence
        self.clock = clock
        self.stage = "initial"
        self.stage_at = clock()
        self.last_received = None
        self.last = None
        self.baseline = None
        self.stopped_steps = None
        self.forward_seen = False
        self.reverse_seen = False
        self.completed = False
        self.commands = []
        self.may_be_running = False

    def start(self):
        self.stage_at = self.clock()

    def send(self, device, command, label):
        # Treat even a failed/partial motion write as possibly received.
        if command in ("f", "r"):
            self.may_be_running = True
        event = dict(command=command, label=label, stage=self.stage, issued_mono=self.clock())
        self.evidence.event("commands.jsonl", dict(kind="intent", **event))
        count = device.write(command.encode("ascii"))
        if count != 1:
            raise OSError("Incomplete Serial command write")
        self.commands.append(event)
        self.evidence.event("commands.jsonl", dict(kind="written", **event))
        print("MOTOR COMMAND: " + label, flush=True)

    def observe(self, payload):
        now = self.clock()
        self.last_received = now
        self.last = dict(payload)
        idle = payload["controller_state"] == "idle" and not payload["coils_energized"]
        if self.stage == "initial":
            if not idle or payload["remaining_steps"] != 0:
                raise RuntimeError("Expected idle motor before motion test")
            self.baseline = payload["commanded_steps"]
            self.stage, self.stage_at = "ready", now
        elif self.stage == "forward":
            delta = (payload["commanded_steps"] - self.baseline) % 2**32
            if (payload["controller_state"] == "stepping" and payload["direction"] == "forward"
                    and payload["coils_energized"] and 0 < delta < 512
                    and payload["remaining_steps"] > 0):
                self.forward_seen = True
        elif self.stage == "stopping":
            if idle and payload["reason"] == "stop_command" and payload["direction"] == "forward":
                delta = (payload["commanded_steps"] - self.baseline) % 2**32
                if not self.forward_seen or not 0 < delta < 512 or payload["remaining_steps"] != 0:
                    raise RuntimeError("Forward partial-stop telemetry was not verified")
                self.stopped_steps = payload["commanded_steps"]
                self.may_be_running = False
                self.stage, self.stage_at = "stopped", now
        elif self.stage == "reverse":
            delta = (payload["commanded_steps"] - self.stopped_steps) % 2**32
            if (payload["controller_state"] == "stepping" and payload["direction"] == "reverse"
                    and payload["coils_energized"] and 0 < delta <= 512):
                self.reverse_seen = True
            if idle and payload["reason"] == "completed" and payload["direction"] == "reverse":
                if not self.reverse_seen or delta != 512 or payload["remaining_steps"] != 0:
                    raise RuntimeError("Reverse completion telemetry was not verified")
                self.completed = True
                self.may_be_running = False
                self.stage, self.stage_at = "complete", now
                print("MOTION TELEMETRY COMPLETE; observe shaft motion separately", flush=True)

    def tick(self, device):
        now = self.clock()
        if self.last_received is not None and now - self.last_received >= 2:
            raise TimeoutError("Serial telemetry stale during motion test")
        age = now - self.stage_at
        if self.stage == "ready" and age >= 2:
            self.send(device, "f", "FORWARD (512-step command)")
            self.stage, self.stage_at = "forward", now
        elif self.stage == "forward" and age >= 1.25:
            self.send(device, "s", "STOP before all 512 forward steps")
            self.stage, self.stage_at = "stopping", now
        elif self.stage == "stopped" and age >= 3:
            self.send(device, "r", "REVERSE (512-step command)")
            self.stage, self.stage_at = "reverse", now
        elif self.stage in ("initial", "stopping", "reverse") and age > 6:
            raise TimeoutError("Motion stage timed out: " + self.stage)

    def finish(self):
        if not self.completed:
            raise RuntimeError("Motion scenario did not complete")

    def close(self, device):
        if self.may_be_running or (self.last and self.last["controller_state"] != "idle"):
            self.send(device, "s", "STOP on exit (not a verified stop acknowledgement)")

    def result(self):
        return dict(stage=self.stage, forward_seen=self.forward_seen,
                    partial_stop_seen=self.stopped_steps is not None,
                    reverse_seen=self.reverse_seen, completed=self.completed,
                    baseline_steps=self.baseline, stopped_steps=self.stopped_steps,
                    commands=self.commands, physical_motion_verified=False)


def aas_motion_coverage(records, scenario):
    """Require fresh readbacks in scenario order, with matching step deltas."""
    phases = []
    for record in records:
        v = record["expected"]
        if v["Freshness"] != "fresh" or not v["HasSample"]:
            continue
        step = v["CommandedSteps"]
        if (not phases and scenario.baseline is not None
                and v["ControllerState"] == "stepping" and v["Direction"] == "forward"
                and v["CoilsEnergized"] and v["RemainingSteps"] > 0
                and 0 < (step - scenario.baseline) % 2**32 < 512):
            phases.append("forward")
        elif (phases == ["forward"] and v["ControllerState"] == "idle"
              and v["Direction"] == "forward" and v["Reason"] == "stop_command"
              and not v["CoilsEnergized"] and v["RemainingSteps"] == 0
              and step == scenario.stopped_steps):
            phases.append("partial_stop")
        elif (phases == ["forward", "partial_stop"] and scenario.stopped_steps is not None
              and v["ControllerState"] == "stepping" and v["Direction"] == "reverse"
              and v["CoilsEnergized"] and 0 < (step - scenario.stopped_steps) % 2**32 <= 512):
            phases.append("reverse")
        elif (phases == ["forward", "partial_stop", "reverse"] and v["ControllerState"] == "idle"
              and v["Direction"] == "reverse" and v["Reason"] == "completed"
              and not v["CoilsEnergized"] and v["RemainingSteps"] == 0
              and (step - scenario.stopped_steps) % 2**32 == 512):
            phases.append("completed")
    return dict(passed=phases == ["forward", "partial_stop", "reverse", "completed"],
                verified_phases=phases)
