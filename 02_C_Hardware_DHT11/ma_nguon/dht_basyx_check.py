"""Combined motor/DHT telemetry. Serial reads only unless --motion is supplied."""
import argparse
import msvcrt

import environment_model
import hardware_model
from environment_tracker import EnvironmentTracker, validate_combined
from hardware_basyx_check import HardwareAdapter, Tracker
from serial_mqtt_check import ROOT, run as transport_run
from motion_scenario import MotionScenario, aas_motion_coverage


def combined_motion_coverage(motor_records, environment_records, control):
    coverage = aas_motion_coverage(motor_records, control)
    # Select the completed reverse move of THIS scenario, not old idle telemetry.
    completed = [r["expected"] for r in motor_records
                 if control.stopped_steps is not None
                 and r["expected"]["Freshness"] == "fresh"
                 and r["expected"]["HasSample"]
                 and r["expected"]["ControllerState"] == "idle"
                 and r["expected"]["Direction"] == "reverse"
                 and r["expected"]["Reason"] == "completed"
                 and not r["expected"]["CoilsEnergized"]
                 and (r["expected"]["CommandedSteps"] - control.stopped_steps) % 2**32 == 512]
    renewed = []
    if completed:
        first = completed[0]
        for record in environment_records:
            v = record["expected"]
            if (v["Freshness"] == "fresh" and v["HasSample"] and v["LatestAttemptValid"]
                    and v["BridgeRunId"] == first["BridgeRunId"]
                    and 0 < (v["LastValidReadUptimeMs"] - first["UptimeMs"]) % 2**32 < 2**31):
                renewed.append(v["LastValidAttempt"])
    return dict(passed=control.completed and coverage["passed"] and bool(renewed),
                motor_coverage=coverage, sensor_attempts_after_completion=sorted(set(renewed)))


class BothObservers:
    def __init__(self, motor, environment):
        self.motor, self.environment = motor, environment
        self.transport_evidence = None

    def accept(self, observed):
        self.environment.accept(observed)
        self.motor.accept(observed)


def run(args):
    evidence = hardware_model.new_evidence("dht-basyx")
    motor_ev = hardware_model.Evidence(evidence.output / "motor")
    env_ev = hardware_model.Evidence(evidence.output / "environment")
    motor_api, env_api = hardware_model.API(motor_ev), environment_model.API(env_ev)
    motor = HardwareAdapter(motor_ev, motor_api, tracker=Tracker(validator=validate_combined))
    env = HardwareAdapter(env_ev, env_api, tracker=EnvironmentTracker(), model=environment_model)
    both = BothObservers(motor, env)
    control = None
    result = dict(passed=False, transport_passed=False, motor_final_stale=False, environment_final_stale=False)
    try:
        hardware_model.bootstrap(motor_api)
        environment_model.bootstrap(env_api)
        motor.start()
        env.start()
        if getattr(args, "motion", False):
            control = MotionScenario(evidence)
            print("MOTION ENABLED: forward, partial stop, reverse with DHT telemetry", flush=True)
        result["transport_passed"] = transport_run(args, observer=both, validator=validate_combined,
                                                  control=control) == 0
        print("Waiting for final stale in BOTH submodels...", flush=True)
        result["motor_final_stale"] = motor.wait_for_final_stale()
        result["environment_final_stale"] = env.wait_for_final_stale()
    except KeyboardInterrupt:
        result["error"] = "stopped_by_user"
    except Exception as exc:
        result["error"] = repr(exc)
    finally:
        for label, adapter in (("motor", motor), ("environment", env)):
            try:
                adapter.close()
            except Exception as exc:
                result[label + "_close_error"] = repr(exc)
        state = env.tracker.snapshot()
        env_fresh = [r for r in env.verified if r["expected"]["Freshness"] == "fresh"
                     and r["expected"]["HasSample"] and r["expected"]["LatestAttemptValid"]]
        distinct = {r["expected"]["LastValidAttempt"] for r in env_fresh}
        motor_fresh = [r for r in motor.verified if r["expected"]["Freshness"] == "fresh"]
        result.update(transport_evidence=both.transport_evidence,
                      sensor_valid_readings=state["valid_readings"], sensor_failed_readings=state["failed_readings"],
                      distinct_sensor_readbacks=len(distinct), motor_fresh_readbacks=len(motor_fresh),
                      motor_write_errors=motor.errors, environment_write_errors=env.errors,
                      final_environment=environment_model.values_for(state),
                      final_motor=hardware_model.values_for(motor.tracker.snapshot()))
        result["passed"] = (result["transport_passed"] and result["motor_final_stale"]
                            and result["environment_final_stale"] and len(distinct) >= 3
                            and len(motor_fresh) >= 3 and state["failed_readings"] == 0
                            and not motor.errors and not env.errors
                            and not any(k.endswith("error") for k in result))
        if control is not None:
            result["motion"] = control.result()
            result["combined_motion"] = combined_motion_coverage(motor.verified, env.verified, control)
            result["passed"] = result["passed"] and result["combined_motion"]["passed"]
        evidence.save("verification.json", result)
        print("DHT AAS " + ("PASS" if result["passed"] else "NOT PASSED"), flush=True)
        if control is not None:
            print("DHT MOTION AAS " + ("PASS" if result["passed"] else "NOT PASSED"), flush=True)
        print("EVIDENCE: " + str(evidence.output), flush=True)
        if "error" in result:
            print("ERROR: " + result["error"], flush=True)
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--motion", action="store_true", help="Send automatic f/s/r motor commands")
    args = parser.parse_args()
    args.samples, args.duration = 40, 35
    # Share the hardware lock with the earlier check; same AAS/Serial owner.
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
