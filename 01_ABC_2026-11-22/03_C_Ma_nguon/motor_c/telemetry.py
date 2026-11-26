"""Stage 2: simulator and MQTT receiver. BaSyx writer is a subsequent stage."""
import argparse
import json
import queue
import threading
import time
import uuid
from pathlib import Path

from freshness import FreshnessTracker, utc_now
from mqtt_local_check import Probe

ROOT = Path(__file__).resolve().parent
TOPIC = 'university/aas-demo/motor-c-stepper/v1/state'

def make_sample(session, started, sequence):
    return {'schema_version': 1, 'source_kind': 'simulator',
            'session_id': session, 'session_started_at': started,
            'sequence': sequence, 'sampled_at': utc_now(),
            'setpoint_speed_rpm': 5.0, 'controller_state': 'stepping',
            'direction': 'forward', 'commanded_steps': sequence * 20}

class Receiver(Probe):
    def __init__(self, topic, checkpoint=None):
        super().__init__('receiver')
        self.topic = topic
        self.tracker = FreshnessTracker(checkpoint)
        self.events = queue.Queue()
        self.stopping = threading.Event()
        self.watcher = None

    def on_connect(self, client, userdata, flags, reason, properties):
        super().on_connect(client, userdata, flags, reason, properties)
        if not reason.is_failure:
            client.subscribe(self.topic, qos=1)

    def on_message(self, client, userdata, message):
        try:
            sample = json.loads(message.payload.decode('utf-8'))
            accepted, reason = self.tracker.accept(sample, retained=message.retain)
        except (ValueError, UnicodeError) as error:
            sample = None
            accepted, reason = False, 'invalid_json: ' + str(error)
        self.events.put({'utc': utc_now(), 'mono': time.monotonic(),
                         'kind': 'accepted' if accepted else 'rejected',
                         'reason': reason, 'retain': message.retain,
                         'payload': sample, 'state': self.tracker.snapshot()})

    def watch(self):
        while not self.stopping.wait(0.05):
            if self.tracker.tick():
                self.events.put({'utc': utc_now(), 'mono': time.monotonic(),
                                 'kind': 'stale', 'state': self.tracker.snapshot()})

    def start(self):
        super().start()
        if not self.subscribed.wait(8) or self.failure:
            raise RuntimeError('Receiver subscription failed: ' + str(self.failure))
        self.watcher = threading.Thread(target=self.watch, daemon=True)
        self.watcher.start()

    def close(self):
        self.stopping.set()
        if self.watcher:
            self.watcher.join(timeout=2)
        super().close()

def run_simulator(stop):
    publisher = Probe('simulator')
    session, started = str(uuid.uuid4()), utc_now()
    sequence = 0
    try:
        publisher.start()
        print('READY simulator C', flush=True)
        while not stop.exists():
            began = time.monotonic()
            sequence += 1
            sample = make_sample(session, started, sequence)
            publisher.publish(TOPIC, sample, retain=False)
            print(json.dumps({'kind': 'published', 'mono': began, 'payload': sample}), flush=True)
            while time.monotonic() - began < 1.0 and not stop.exists():
                time.sleep(.05)
    finally:
        publisher.close()

def run_receiver(stop):
    run_id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    output = ROOT / 'evidence' / ('receiver-' + run_id)
    output.mkdir(parents=True)
    receiver = Receiver(TOPIC, ROOT / 'state' / 'receiver-checkpoint.json')
    try:
        receiver.start()
        print('READY receiver C; EVIDENCE: ' + str(output), flush=True)
        with (output / 'events.jsonl').open('a', encoding='utf-8') as log:
            while not stop.exists():
                try:
                    event = receiver.events.get(timeout=.2)
                except queue.Empty:
                    continue
                line = json.dumps(event)
                print(line, flush=True)
                log.write(line + '\n')
                log.flush()
                if event['kind'] in ('accepted', 'stale'):
                    receiver.tracker.save()
    finally:
        receiver.close()
        receiver.tracker.save()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('simulator', 'receiver'))
    parser.add_argument('--stop-file', required=True, type=Path)
    parser.add_argument('--gate', action='store_true')
    args = parser.parse_args()
    if args.gate:
        import sys
        if sys.stdin.readline().strip() != 'GO':
            raise SystemExit(1)
    if args.mode == 'simulator':
        run_simulator(args.stop_file)
    else:
        run_receiver(args.stop_file)
