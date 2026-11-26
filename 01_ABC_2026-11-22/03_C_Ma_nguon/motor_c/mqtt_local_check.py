"""Known-payload transport check on a unique topic. Does not access BaSyx."""
import json
import msvcrt
import queue
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parent

class Probe:
    def __init__(self, name):
        self.connected = threading.Event()
        self.subscribed = threading.Event()
        self.messages = queue.Queue()
        self.failure = None
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id='motor-c-probe-' + name + '-' + uuid.uuid4().hex[:8])
        self.client.on_connect = self.on_connect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure:
            self.failure = str(reason)
        self.connected.set()

    def on_subscribe(self, client, userdata, mid, reasons, properties):
        if any(reason.is_failure for reason in reasons):
            self.failure = str(reasons)
        self.subscribed.set()

    def on_message(self, client, userdata, message):
        self.messages.put({'received_at': datetime.now(timezone.utc).isoformat(),
                           'topic': message.topic, 'qos': message.qos,
                           'retain': message.retain,
                           'payload': json.loads(message.payload.decode('utf-8'))})

    def start(self):
        self.client.connect('127.0.0.1', 1883, keepalive=15)
        self.client.loop_start()
        assert self.connected.wait(8), 'CONNACK timeout'
        assert self.failure is None, self.failure

    def subscribe(self, topic):
        result, _ = self.client.subscribe(topic, qos=1)
        assert result == mqtt.MQTT_ERR_SUCCESS
        assert self.subscribed.wait(8), 'SUBACK timeout'
        assert self.failure is None, self.failure

    def publish(self, topic, payload, retain=False):
        body = '' if payload is None else json.dumps(payload, separators=(',', ':'))
        info = self.client.publish(topic, body, qos=1, retain=retain)
        info.wait_for_publish(timeout=8)
        assert info.is_published(), 'PUBACK timeout'

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()

def main():
    run_id = uuid.uuid4().hex[:10]
    topic = 'university/aas-demo/motor-c-stepper/v1/probe/' + run_id
    output = ROOT / 'evidence' / ('mqtt-local-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + run_id)
    output.mkdir(parents=True)
    publisher, subscriber, late = Probe('pub'), Probe('sub'), Probe('late')
    sample = {'schema_version': 1, 'source_kind': 'simulator', 'session_id': run_id,
              'sequence': 1, 'sampled_at': datetime.now(timezone.utc).isoformat(),
              'setpoint_speed_rpm': 5.0, 'controller_state': 'stepping',
              'direction': 'forward', 'commanded_steps': 120}
    records = []
    result = {'passed': False, 'topic': topic, 'basyx_accessed': False,
              'application_freshness_tested': False}
    try:
        publisher.start()
        subscriber.start()
        subscriber.subscribe(topic)
        # Publish order 1, 1, 0 deliberately exercises transport of duplicate
        # and out-of-order application samples, not an MQTT delivery fault.
        for label, payload in [('known_payload', sample), ('duplicate_sample', sample),
                               ('out_of_order_sample', dict(sample, sequence=0))]:
            publisher.publish(topic, payload)
            observed = subscriber.messages.get(timeout=8)
            assert observed['payload'] == payload
            assert observed['qos'] == 1 and observed['retain'] is False
            records.append({'case': label, 'sent': payload, 'observed': observed, 'transport_match': True})
            print('PASS transport:', label, flush=True)
        subscriber.close()
        old_sample = dict(sample, sequence=2, sampled_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
        publisher.publish(topic, old_sample, retain=True)
        late.start()
        late.subscribe(topic)
        replay = late.messages.get(timeout=8)
        assert replay['payload'] == old_sample and replay['retain'] is True
        records.append({'case': 'old_retained_replay', 'sent': old_sample,
                        'observed': replay, 'transport_match': True})
        print('PASS transport: old retained sample delivered with retain=true', flush=True)
        result['passed'] = True
    except Exception as error:
        result['error'] = repr(error)
        raise
    finally:
        # Unsubscribe the late receiver before publishing the empty retained
        # deletion, which is intentionally not a JSON state sample.
        late.close()
        subscriber.close()
        try:
            if publisher.client.is_connected():
                publisher.publish(topic, None, retain=True)
                result['retained_probe_cleared'] = True
        finally:
            publisher.close()
            (output/'messages.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            (output/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print('EVIDENCE:', output, flush=True)
    print('MQTT LOCAL PASS. Freshness filtering and timeout are separate adapter tests.', flush=True)

if __name__ == '__main__':
    with (ROOT / 'mqtt-check.lock').open('a+b') as lock_file:
        if lock_file.tell() == 0:
            lock_file.write(b'0')
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('MQTT local check is already running in this workspace')
        try:
            main()
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
