"""Đọc JSON từ MotorC_SerialTelemetry; không gửi lệnh hoặc publish MQTT.

Chạy --self-test để kiểm tra parser offline, không mở cổng Serial.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import time
from pathlib import Path
import uuid


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate_key: {key}")
        result[key] = value
    return result


def validate_line(raw):
    """Kiểm tra kiểu/giới hạn của 8 trường; bool không được tính là int."""
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(data, dict):
        raise ValueError("expected_object")
    integers = {"sequence": 2**32 - 1, "uptime_ms": 2**32 - 1,
                "commanded_steps": 2**32 - 1, "remaining_steps": 512}
    enums = {"controller_state": ("idle", "stepping"),
             "direction": ("none", "forward", "reverse"),
             "reason": ("boot", "start_command", "stop_command", "completed")}
    expected = set(integers) | set(enums) | {"coils_energized"}
    if set(data) != expected:
        raise ValueError("unexpected_or_missing_fields")
    for key, maximum in integers.items():
        if type(data[key]) is not int or not 0 <= data[key] <= maximum:
            raise ValueError(f"invalid_integer: {key}")
    for key, choices in enums.items():
        if data[key] not in choices:
            raise ValueError(f"invalid_enum: {key}")
    if type(data["coils_energized"]) is not bool:
        raise ValueError("invalid_boolean: coils_energized")
    return data


class Lines:
    """Ghép byte đến khi có LF; không nhầm timeout thành cuối dòng.

    Dòng quá dài được bỏ đến LF để giới hạn RAM; raw.bin vẫn giữ mọi byte.
    """
    def __init__(self, limit=4096):
        self.pending = bytearray()
        self.overflow = False
        self.limit = limit

    def feed(self, chunk):
        for byte in chunk:
            if byte == 10:
                yield bytes(self.pending), self.overflow
                self.pending.clear()
                self.overflow = False
            elif not self.overflow:
                if len(self.pending) >= self.limit:
                    self.overflow = True
                    self.pending.clear()
                else:
                    self.pending.append(byte)


class Freshness:
    """Receive freshness only; never infer physical motor state."""
    def __init__(self, timeout, now):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("stale timeout must be finite and positive")
        self.timeout = timeout
        self.started = now
        self.last_valid = None
        self.state = "waiting"

    def update(self, now, valid=False):
        if valid:
            self.last_valid = now
        reference = self.started if self.last_valid is None else self.last_valid
        state = ("stale" if now - reference >= self.timeout else
                 "waiting" if self.last_valid is None else "fresh")
        if state == self.state:
            return None
        self.state = state
        return dict(state=state, age_seconds=None if self.last_valid is None
                    else round(now - self.last_valid, 6))


def self_test():
    # Dữ liệu tổng hợp chỉ để kiểm parser, KHÔNG là bằng chứng phần cứng.
    sample = dict(sequence=1, uptime_ms=500, controller_state="idle",
                  direction="none", commanded_steps=0, remaining_steps=0,
                  coils_energized=False, reason="boot")
    raw = json.dumps(sample).encode()
    assert validate_line(raw) == sample
    for bad in [b"bad", b"[]", raw.replace(b'"sequence": 1', b'"sequence": true'),
                raw.replace(b'"remaining_steps": 0', b'"remaining_steps": 513'),
                raw.replace(b'"reason": "boot"', b'"reason": "unknown"'),
                b'{"sequence":1,"sequence":2}']:
        try:
            validate_line(bad)
        except (ValueError, UnicodeError):
            pass
        else:
            raise AssertionError(f"accepted invalid line: {bad!r}")
    lines = Lines()
    assert list(lines.feed(raw[:30])) == []
    assert list(lines.feed(b"")) == []
    assert list(lines.feed(raw[30:] + b"\r\n")) == [(raw + b"\r", False)]
    lines = Lines(3)
    assert list(lines.feed(b"abcd\nx\n")) == [(b"", True), (b"x", False)]
    print("PASS: offline parser/framing tests; no Serial port opened.")
    watchdog = Freshness(2.0, 0.0)
    assert watchdog.update(1.9) is None
    assert watchdog.update(2.0)["state"] == "stale"
    assert watchdog.update(2.1) is None
    assert watchdog.update(3.0, valid=True)["state"] == "fresh"
    assert watchdog.update(4.9) is None
    assert watchdog.update(5.0)["state"] == "stale"
    assert watchdog.update(6.0, valid=True)["state"] == "fresh"
    for timeout in [0, -1, float("nan"), float("inf")]:
        try:
            Freshness(timeout, 0)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid timeout accepted")
    print("PASS: waiting, timeout boundary, silence, recovery, timeout validation.")


def capture(args, serial):
    # Mỗi lượt có thư mục riêng. Capture ID của PC KHÔNG là SessionId của ESP.
    capture_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    folder = args.output / ("serial-c-" + capture_id)
    folder.mkdir(parents=True, exist_ok=False)
    summary = dict(capture_id=capture_id, started_at=utc_now(), port=args.port,
                   baud=args.baud, received_bytes=0, complete_lines=0,
                   valid_lines=0, invalid_lines=0, sequence_warnings=0,
                   status="opening", error=None)
    lines, previous = Lines(), None
    device = None
    watchdog = None
    events = None
    summary.update(stale_after_seconds=args.stale_after, freshness="waiting",
                   freshness_transitions=0, last_valid_received_at=None)

    def update_freshness(valid=False):
        event = watchdog.update(time.monotonic(), valid)
        if valid:
            summary["last_valid_received_at"] = utc_now()
        summary["freshness"] = watchdog.state
        if event is not None:
            event["pc_observed_at"] = utc_now()
            summary["freshness_transitions"] += 1
            events.write(json.dumps(event) + "\n")
            events.flush()
            print(f"DATA {event['state'].upper()}", flush=True)
    try:
        # Driver có thể đổi DTR/RTS khi mở cổng, có thể làm board reset.
        # Không gọi write(), không gửi f/r/s; không tự reconnect khi rút USB.
        device = serial.Serial(args.port, args.baud, timeout=0.25)
        summary["status"] = "capturing"
        watchdog = Freshness(args.stale_after, time.monotonic())
        events = (folder / "freshness.jsonl").open("w", encoding="utf-8")
        print(f"READ ONLY {args.port} @ {args.baud}; Ctrl+C to finish.\n{folder}", flush=True)
        with (folder / "raw.bin").open("wb") as raw_file, (folder / "records.jsonl").open("w", encoding="utf-8") as records:
            while True:
                chunk = device.read(1024)
                update_freshness()
                if not chunk:
                    continue
                raw_file.write(chunk)
                raw_file.flush()
                summary["received_bytes"] += len(chunk)
                for raw, overflow in lines.feed(chunk):
                    summary["complete_lines"] += 1
                    record = dict(line=summary["complete_lines"], pc_received_at=utc_now())
                    try:
                        if overflow:
                            raise ValueError("line_exceeds_4096_bytes")
                        data = validate_line(raw)
                        update_freshness(valid=True)
                        record.update(valid=True, payload=data)
                        summary["valid_lines"] += 1
                        if previous is not None and data["sequence"] != (previous + 1) % (2**32):
                            record["warning"] = "sequence_discontinuity_reset_or_gap_or_reorder"
                            summary["sequence_warnings"] += 1
                        previous = data["sequence"]
                        print(f"seq={data['sequence']} state={data['controller_state']} steps={data['commanded_steps']} remaining={data['remaining_steps']}", flush=True)
                    except (ValueError, UnicodeError) as exc:
                        record.update(valid=False, error=str(exc))
                        summary["invalid_lines"] += 1
                        print(f"INVALID line {record['line']}: {exc}", flush=True)
                    records.write(json.dumps(record, ensure_ascii=False) + "\n")
                    records.flush()
    except KeyboardInterrupt:
        summary["status"] = "stopped_by_user"
    except (serial.SerialException, OSError) as exc:
        summary.update(status="error", error=str(exc))
        print(f"ERROR: {exc}", flush=True)
    finally:
        if events is not None:
            events.close()
        if device is not None:
            try:
                device.close()
            except (serial.SerialException, OSError) as exc:
                summary.update(status="error", error=str(exc))
        summary.update(ended_at=utc_now(), trailing_partial_bytes=len(lines.pending),
                       trailing_line_overflow=lines.overflow)
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Summary: {folder / 'summary.json'}", flush=True)
    return 1 if summary["status"] == "error" else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "evidence")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--stale-after", type=float, default=2.0)
    args = parser.parse_args()
    if not math.isfinite(args.stale_after) or args.stale_after <= 0:
        parser.error("--stale-after must be finite and positive")
    if args.self_test:
        self_test()
        return 0
    try:
        import serial
    except ImportError:
        parser.exit(2, "Missing pyserial: install motor_c/requirements-serial.txt using the project Python.\n")
    return capture(args, serial)


if __name__ == "__main__":
    raise SystemExit(main())
