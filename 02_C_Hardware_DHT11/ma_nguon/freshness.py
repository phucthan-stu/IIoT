"""Validate controller telemetry and track receipt freshness independently of I/O."""
import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

STALE_SECONDS = 5.0
MAX_SAMPLE_AGE_SECONDS = 2.0
MAX_FUTURE_SECONDS = 0.5

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp must be text with UTC offset')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp requires timezone')
    return parsed.timestamp()

def validate(sample, now):
    if not isinstance(sample, dict) or type(sample.get('schema_version')) is not int or sample['schema_version'] != 1:
        raise ValueError('schema_version must be integer 1')
    if sample.get('source_kind') != 'simulator':
        raise ValueError('This milestone accepts simulator telemetry only')
    session = sample.get('session_id')
    if not isinstance(session, str) or not 1 <= len(session) <= 128:
        raise ValueError('Invalid session_id')
    for name in ('sequence', 'commanded_steps'):
        if type(sample.get(name)) is not int or sample[name] < (1 if name == 'sequence' else 0):
            raise ValueError('Invalid ' + name)
    speed = sample.get('setpoint_speed_rpm')
    if type(speed) not in (int, float) or not math.isfinite(speed) or speed < 0:
        raise ValueError('Invalid setpoint speed')
    if sample.get('controller_state') not in ('idle', 'stepping'):
        raise ValueError('Invalid controller_state')
    if sample.get('direction') not in ('forward', 'reverse'):
        raise ValueError('Invalid direction')
    sampled = timestamp(sample.get('sampled_at'))
    started = timestamp(sample.get('session_started_at'))
    if started > sampled:
        raise ValueError('Session starts after sample')
    if now - sampled > MAX_SAMPLE_AGE_SECONDS:
        raise ValueError('sample_expired')
    if sampled - now > MAX_FUTURE_SECONDS:
        raise ValueError('sample_from_future')
    return sampled, started

class FreshnessTracker:
    def __init__(self, checkpoint=None):
        self.lock = threading.Lock()
        self.checkpoint = Path(checkpoint) if checkpoint else None
        self.sample = None
        self.received_at = None
        self.last_mono = None
        self.quality = 'waiting'
        self.retired_sessions = set()
        if self.checkpoint and self.checkpoint.exists():
            stored = json.loads(self.checkpoint.read_text(encoding='utf-8'))
            self.sample = stored['sample']
            self.received_at = stored['last_received_at']
            self.retired_sessions = set(stored['retired_sessions'])
            # A process restart / reading a checkpoint never renews freshness.
            self.quality = 'stale' if self.sample else 'waiting'

    def accept(self, sample, retained=False):
        now = datetime.now(timezone.utc).timestamp()
        mono = time.monotonic()
        with self.lock:
            if retained:
                return False, 'retained_replay'
            try:
                sampled, started = validate(sample, now)
            except (ValueError, TypeError, KeyError) as error:
                return False, str(error)
            if sample['session_id'] in self.retired_sessions:
                return False, 'retired_session'
            if self.sample:
                previous = self.sample
                if sample['session_id'] == previous['session_id']:
                    if sample['session_started_at'] != previous['session_started_at']:
                        return False, 'session_metadata_changed'
                    if sample['sequence'] <= previous['sequence']:
                        return False, 'duplicate_or_out_of_order'
                    if sampled <= timestamp(previous['sampled_at']):
                        return False, 'sample_time_not_increasing'
                else:
                    if started <= timestamp(previous['session_started_at']) or sampled <= timestamp(previous['sampled_at']):
                        return False, 'older_session'
                    self.retired_sessions.add(previous['session_id'])
            self.sample = dict(sample)
            self.received_at = utc_now()
            self.last_mono = mono
            self.quality = 'fresh'
            return True, 'new_sample'

    def tick(self):
        with self.lock:
            if self.quality == 'fresh' and time.monotonic() - self.last_mono >= STALE_SECONDS:
                self.quality = 'stale'
                return True
            return False

    def snapshot(self):
        with self.lock:
            return {'sample': dict(self.sample) if self.sample else None,
                    'last_received_at': self.received_at, 'freshness': self.quality,
                    'last_received_mono': self.last_mono,
                    'retired_sessions': sorted(self.retired_sessions)}

    def save(self):
        # Disk I/O is outside the lock used by the deadline watcher.
        if self.checkpoint:
            data = self.snapshot()
            self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.checkpoint.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
            temporary.replace(self.checkpoint)
