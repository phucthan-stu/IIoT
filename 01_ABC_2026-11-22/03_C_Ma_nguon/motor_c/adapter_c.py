"""MQTT/freshness receiver with an independent, latest-state BaSyx writer."""
import argparse
import json
import queue
import threading
import time
from pathlib import Path

from basyx_c import API, SM_PATH, SHELL_PATH, new_evidence, values_for, verify_metadata, verify_shell, verify_values
from telemetry import Receiver, TOPIC, ROOT

class Adapter:
    def __init__(self, evidence, topic=TOPIC, checkpoint=None, api=None):
        self.evidence = evidence
        self.api = api or API(evidence)
        self.receiver = Receiver(topic, checkpoint)
        self.stop_event = threading.Event()
        self.writer = None
        self.recorder = None
        self.verified = queue.Queue()

    def start(self):
        verify_metadata(self.api.get(SM_PATH))
        verify_shell(self.api.get(SHELL_PATH))
        self.receiver.start()
        self.recorder = threading.Thread(target=self.record_events, daemon=True)
        self.writer = threading.Thread(target=self.write_latest, daemon=True)
        self.recorder.start()
        self.writer.start()

    def record_events(self):
        while not self.stop_event.is_set() or not self.receiver.events.empty():
            try:
                event = self.receiver.events.get(timeout=.1)
            except queue.Empty:
                continue
            self.evidence.event('receiver.jsonl', event)
            print(json.dumps({'kind': event['kind'], 'reason': event.get('reason'),
                              'freshness': event['state']['freshness'],
                              'last_received_at': event['state']['last_received_at']}), flush=True)
            if event['kind'] in ('accepted', 'stale'):
                self.receiver.tracker.save()

    def write_latest(self):
        last_verified = None
        while not self.stop_event.is_set():
            # Only snapshot holds the tracker lock. HTTP and evidence I/O do not.
            state = self.receiver.tracker.snapshot()
            if not state['sample']:
                self.stop_event.wait(.05)
                continue
            expected = values_for(state)
            if expected == last_verified:
                self.stop_event.wait(.05)
                continue
            started = time.monotonic()
            try:
                status, _ = self.api.request('PATCH', SM_PATH+'/$value', expected)
                if status not in (200, 204):
                    raise RuntimeError('C PATCH failed: '+str(status))
                actual = verify_values(self.api.get(SM_PATH), expected)
                last_verified = expected
                record = {'kind': 'verified', 'expected': expected, 'observed': actual,
                          'started_mono': started, 'completed_mono': time.monotonic(),
                          'receiver_now': self.receiver.tracker.snapshot()['freshness']}
                self.evidence.event('writes.jsonl', record)
                self.verified.put(record)
                print(json.dumps({'kind': 'basyx_verified', 'sequence': expected['Sequence'],
                                  'freshness_written': expected['Freshness'],
                                  'receiver_now': record['receiver_now']}), flush=True)
            except Exception as error:
                self.evidence.event('writes.jsonl', {'kind': 'write_error', 'error': repr(error),
                                                     'expected': expected, 'started_mono': started})
                print('C writer error: '+str(error), flush=True)
                self.stop_event.wait(.5)

    def close(self):
        self.receiver.close()
        self.stop_event.set()
        for thread in (self.writer, self.recorder):
            if thread:
                thread.join(timeout=30)
                if thread.is_alive():
                    raise RuntimeError('Adapter worker did not stop within bound')
        self.receiver.tracker.save()

def main(stop):
    evidence = new_evidence('adapter-c')
    adapter = Adapter(evidence, checkpoint=ROOT/'state'/'adapter-checkpoint.json')
    try:
        adapter.start()
        print('READY adapter C; EVIDENCE: '+str(evidence.output), flush=True)
        while not stop.exists():
            time.sleep(.1)
    finally:
        adapter.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stop-file', type=Path, required=True)
    parser.add_argument('--gate', action='store_true')
    args = parser.parse_args()
    if args.gate:
        import sys
        if sys.stdin.readline().strip() != 'GO':
            raise SystemExit(1)
    main(args.stop_file)
