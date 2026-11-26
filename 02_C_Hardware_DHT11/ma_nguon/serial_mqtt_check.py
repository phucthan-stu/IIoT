"""Bounded hardware transport check: USB Serial -> MQTT -> independent subscriber.

Default mode sends no motor commands. Optional controller is explicitly supplied
by the motion check. MQTT waiting runs outside the Serial loop.
"""
import argparse
import json
import queue
import threading
import time
import uuid
from pathlib import Path

from mqtt_local_check import Probe
from serial_logger import Freshness, Lines, utc_now, validate_line

ROOT = Path(__file__).resolve().parent


def envelope(payload, received_at, run_id, index):
    return dict(schema_version=1, source_kind="esp32_serial",
                bridge_run_id=run_id, bridge_index=index,
                pc_received_at=received_at, telemetry=payload)


def run(args, observer=None, control=None, validator=validate_line):
    import serial
    run_id = uuid.uuid4().hex
    folder = ROOT / "evidence" / ("serial-mqtt-" + run_id)
    folder.mkdir(parents=True, exist_ok=False)
    topic = "university/aas-demo/motor-c-stepper/v1/serial-check/" + run_id
    summary = dict(run_id=run_id, started_at=utc_now(), topic=topic,
                   source_kind="esp32_serial", passed=False, requested_samples=args.samples,
                   received_bytes=0, complete_lines=0, valid_lines=0, invalid_lines=0,
                   sequence_warnings=0, verified_messages=0, duplicate_deliveries=0,
                   status="starting", errors=[], basyx_accessed=False)
    if observer is not None:
        observer.transport_evidence = str(folder)
        summary["basyx_accessed"] = True
    publisher, subscriber = Probe("serial-pub"), Probe("serial-sub")
    pending = queue.Queue(maxsize=64)
    done, failed = threading.Event(), threading.Event()
    worker_errors = []
    device = None
    worker = None
    framing = Lines()
    watchdog = None

    def mqtt_worker():
        seen = set()
        try:
            with (folder / "mqtt.jsonl").open("w", encoding="utf-8") as log:
                while not done.is_set() or not pending.empty():
                    try:
                        item = pending.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    publisher.publish(topic, item, retain=False)
                    deadline = time.monotonic() + 8
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise RuntimeError("Subscriber receive timeout")
                        observed = subscriber.messages.get(timeout=remaining)
                        body = observed["payload"]
                        if body.get("bridge_index") in seen:
                            summary["duplicate_deliveries"] += 1
                            log.write(json.dumps(dict(kind="duplicate", observed=observed)) + "\n")
                            log.flush()
                            continue
                        if observed["topic"] != topic or body != item:
                            raise RuntimeError("MQTT received payload/topic mismatch")
                        if observed["qos"] != 1 or observed["retain"]:
                            raise RuntimeError("Unexpected QoS/retain")
                        break
                    seen.add(item["bridge_index"])
                    if observer is not None:
                        observer.accept(observed)
                    summary["verified_messages"] += 1
                    log.write(json.dumps(dict(kind="verified", sent=item, observed=observed)) + "\n")
                    log.flush()
                    print(f"MQTT MATCH seq={item['telemetry']['sequence']}", flush=True)
        except Exception as exc:
            worker_errors.append(repr(exc))
            failed.set()

    try:
        print(f"EVIDENCE: {folder}\nTOPIC: {topic}", flush=True)
        subscriber.start()
        subscriber.subscribe(topic)
        publisher.start()
        print("MQTT READY: publisher connected, subscriber subscribed", flush=True)
        worker = threading.Thread(target=mqtt_worker, name="mqtt-transport")
        worker.start()
        device = serial.Serial(args.port, 115200, timeout=0.25, write_timeout=0.5)
        if control is not None:
            control.start()
        began = time.monotonic()
        watchdog = Freshness(2.0, began)
        previous = None
        summary["status"] = "capturing"
        with ((folder / "raw.bin").open("wb") as raw_file,
              (folder / "serial.jsonl").open("w", encoding="utf-8") as records,
              (folder / "freshness.jsonl").open("w", encoding="utf-8") as events):

            def freshness(valid=False):
                event = watchdog.update(time.monotonic(), valid)
                if event:
                    event["pc_observed_at"] = utc_now()
                    events.write(json.dumps(event) + "\n")
                    events.flush()
                    print("SERIAL DATA " + event["state"].upper(), flush=True)

            while summary["valid_lines"] < args.samples:
                if failed.is_set():
                    raise RuntimeError("MQTT worker failed")
                if time.monotonic() - began >= args.duration:
                    raise TimeoutError("Not enough valid Serial samples before deadline")
                if control is not None:
                    control.tick(device)
                chunk = device.read(1024)
                freshness()
                if not chunk:
                    continue
                raw_file.write(chunk)
                raw_file.flush()
                summary["received_bytes"] += len(chunk)
                for raw, overflow in framing.feed(chunk):
                    summary["complete_lines"] += 1
                    record = dict(line=summary["complete_lines"], pc_received_at=utc_now())
                    try:
                        if overflow:
                            raise ValueError("line_exceeds_4096_bytes")
                        payload = validator(raw)
                    except (ValueError, UnicodeError) as exc:
                        summary["invalid_lines"] += 1
                        record.update(valid=False, error=str(exc))
                    else:
                        freshness(valid=True)
                        summary["valid_lines"] += 1
                        record.update(valid=True, payload=payload)
                        if previous is not None and payload["sequence"] != (previous + 1) % 2**32:
                            summary["sequence_warnings"] += 1
                            record["warning"] = "sequence_discontinuity"
                        previous = payload["sequence"]
                    records.write(json.dumps(record) + "\n")
                    records.flush()
                    if record["valid"]:
                        pending.put_nowait(envelope(payload, record["pc_received_at"],
                                                  run_id, summary["valid_lines"]))
                        if control is not None:
                            control.observe(payload)
            if control is not None:
                control.finish()
            summary["status"] = "captured"
    except KeyboardInterrupt:
        summary["status"] = "stopped_by_user"
    except Exception as exc:
        summary["status"] = "error"
        summary["errors"].append(repr(exc))
    finally:
        if device is not None:
            if control is not None:
                try:
                    control.close(device)
                except Exception as exc:
                    summary["errors"].append("Stop-on-exit failed: " + repr(exc))
            try:
                device.close()
            except Exception as exc:
                summary["errors"].append(repr(exc))
        done.set()
        if worker is not None:
            # Each queued item either completes or fails with a bounded timeout.
            worker.join()
        summary["errors"].extend(worker_errors)
        for client in (publisher, subscriber):
            try:
                client.close()
            except Exception as exc:
                summary["errors"].append(repr(exc))
        summary.update(ended_at=utc_now(), trailing_partial_bytes=len(framing.pending),
                       trailing_line_overflow=framing.overflow,
                       serial_freshness=watchdog.state if watchdog else "waiting")
        if control is not None:
            summary["motion"] = control.result()
        summary["passed"] = (summary["status"] == "captured" and not summary["errors"]
                             and summary["valid_lines"] >= args.samples
                             and summary["verified_messages"] == summary["valid_lines"]
                             and summary["invalid_lines"] == 0
                             and summary["sequence_warnings"] == 0
                             and not framing.pending and not framing.overflow)
        (folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print("SERIAL MQTT " + ("PASS" if summary["passed"] else "NOT PASSED"), flush=True)
        print(f"SUMMARY: {folder / 'summary.json'}", flush=True)
        if summary["errors"]:
            print("ERRORS: " + repr(summary["errors"]), flush=True)
    return 0 if summary["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.samples <= 50 or not 5 <= args.duration <= 60:
        parser.error("samples must be 1..50; duration must be 5..60 seconds")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
