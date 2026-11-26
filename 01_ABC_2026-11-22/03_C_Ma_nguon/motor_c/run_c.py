"""Own only Motor C child processes, reusing the workspace's Windows Job cleanup."""
import os
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
import demo

STATE = ROOT / 'state'
STATE.mkdir(exist_ok=True)

def active(mode):
    return demo.busy('motor-c-' + mode)

def ensure_broker():
    from mqtt_local_check import Probe
    broker_lock = demo.Lock('motor-c-broker-start')
    try:
        broker = json.loads(demo.docker('inspect', 'motorc_mqtt-broker-1'))[0]
        expected_image = 'sha256:75d6662dee52c947dc9dd9c29f1d8d7ffef78ddaf38001caa09755681e8bbc98'
        if broker['Image'] != expected_image or broker['Config']['Labels'].get('iiot.workspace') != 'motor-c-local':
            raise RuntimeError('MQTT container differs from the approved local broker')
        expected_ports = {'1883/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '1883'}]}
        if broker['HostConfig']['PortBindings'] != expected_ports:
            raise RuntimeError('MQTT port binding differs from approved localhost configuration')
        volumes = {m['Name'] for m in broker['Mounts'] if m['Type'] == 'volume'}
        for container_id in demo.docker('ps', '-q', '--no-trunc').split():
            if container_id == broker['Id']:
                continue
            other = json.loads(demo.docker('inspect', container_id))[0]
            if any(m.get('Name') in volumes for m in other['Mounts']):
                raise RuntimeError('Another running container uses the broker volumes')
        if not broker['State']['Running']:
            print('MQTT broker is stopped. Starting the existing local broker...', flush=True)
            demo.docker('start', 'motorc_mqtt-broker-1')
        def handshake():
            probe = Probe('readiness')
            try:
                probe.start()
                return True
            finally:
                probe.close()
        demo.wait('MQTT 127.0.0.1:1883', handshake, 15)
        print('MQTT broker READY at 127.0.0.1:1883', flush=True)
    finally:
        broker_lock.close()

def main(mode):
    gate = demo.Lock('motor-c-admission')
    if mode == 'stop':
        try:
            modes = [name for name in ('simulator', 'adapter', 'receiver') if active(name)]
            for name in modes:
                (STATE / (name + '.stop')).write_text('stop C task', encoding='utf-8')
        finally:
            gate.close()
        demo.wait('C tasks stopped', lambda: not any(active(name) for name in modes), 20)
        print('Stopped C tasks:', modes)
        return
    try:
        if mode == 'simulator' and not any(active(name) and (STATE/(name+'.ready')).exists() for name in ('receiver','adapter')):
            raise RuntimeError('Start task 10 receiver or task 14 adapter and wait for READY before task 11')
        other = {'receiver': 'adapter', 'adapter': 'receiver'}.get(mode)
        if other and active(other):
            raise RuntimeError('Stop '+other+' before starting '+mode)
        lock = demo.Lock('motor-c-' + mode)
        stop = STATE / (mode + '.stop')
        ready = STATE / (mode + '.ready')
        stop.unlink(missing_ok=True)
        ready.unlink(missing_ok=True)
    finally:
        gate.close()
    job = None
    process = None
    output_thread = None
    try:
        ensure_broker()
        if stop.exists():
            raise RuntimeError('C task cancelled during broker readiness check')
        job = demo.Job()
        if mode == 'adapter':
            command = [demo.C['python'], '-B', str(ROOT/'adapter_c.py'), '--stop-file', str(stop), '--gate']
        else:
            command = [demo.C['python'], '-B', str(ROOT/'telemetry.py'), mode,
                       '--stop-file', str(stop), '--gate']
        environment = dict(os.environ, PYTHONIOENCODING='utf-8')
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=environment,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        job.assign(process)
        def forward():
            for raw in iter(process.stdout.readline, b''):
                line = raw.decode('utf-8', errors='replace')
                print(line, end='', flush=True)
                if line.startswith('READY '):
                    ready.write_text(str(process.pid), encoding='utf-8')
            process.stdout.close()
        output_thread = threading.Thread(target=forward, daemon=True)
        output_thread.start()
        process.stdin.write(b'GO\n')
        process.stdin.close()
        while process.poll() is None and not stop.exists():
            time.sleep(.1)
        if process.poll() is not None and process.returncode:
            raise RuntimeError('C child failed with exit code ' + str(process.returncode))
    finally:
        stop.write_text('end C task', encoding='utf-8')
        if process and process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        if job:
            job.close()
        if process:
            process.wait(timeout=5)
        if output_thread:
            output_thread.join(timeout=2)
        ready.unlink(missing_ok=True)
        lock.close()

if __name__ == '__main__':
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ('simulator', 'receiver', 'adapter', 'stop'):
            raise ValueError('Expected simulator, receiver, adapter, or stop')
        main(sys.argv[1])
    except KeyboardInterrupt:
        print('C task stopped and owned child cleaned up')
    except Exception as error:
        print('STOP:', error, file=sys.stderr)
        sys.exit(1)
