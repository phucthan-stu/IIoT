"""Explicit C-only bootstrap and HTTP access; never modifies A/B resources."""
import base64
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from freshness import timestamp, utc_now
STALE_SECONDS = 2.0

ROOT = Path(__file__).resolve().parent
SHELL_ID = 'urn:university:aas-demo:shell:motor-c-stepper-hardware'
SUBMODEL_ID = 'urn:university:aas-demo:submodel:motor-c-stepper:hardware-telemetry'
ASSET_ID = 'urn:university:aas-demo:asset:motor-c-stepper'
EPOCH = '1970-01-01T00:00:00+00:00'
# (AAS type, initial value, description)
FIELDS = {
    'SourceKind': ('xs:string', 'esp32_serial', 'Hardware controller telemetry received via USB Serial and MQTT.'),
    'ControllerState': ('xs:string', 'unknown', 'Firmware controller state; not proof of shaft motion.'),
    'Direction': ('xs:string', 'unknown', 'Commanded direction; none means no move command since boot.'),
    'CommandedSteps': ('xs:integer', 0, 'Steps emitted by firmware since boot; not measured shaft position.'),
    'RemainingSteps': ('xs:integer', 0, 'Firmware remaining steps for the active command.'),
    'CoilsEnergized': ('xs:boolean', False, 'Firmware GPIO command state; not a current measurement.'),
    'Reason': ('xs:string', 'unknown', 'Last firmware state-change reason.'),
    'Sequence': ('xs:integer', 0, 'ESP32 message sequence; not a step count.'),
    'UptimeMs': ('xs:integer', 0, 'ESP32 uptime in milliseconds, not UTC.'),
    'BridgeRunId': ('xs:string', '', 'Python bridge capture ID; NOT ESP32 boot session ID.'),
    'BridgeIndex': ('xs:integer', 0, 'Increasing bridge message index within this bridge run.'),
    'PCReceivedAt': ('xs:dateTime', EPOCH, 'UTC when PC processed the complete Serial line; not source sample UTC.'),
    'LastReceivedAt': ('xs:dateTime', EPOCH, 'UTC of last newly accepted MQTT message; GET never renews this time.'),
    'Freshness': ('xs:string', 'waiting', 'Receipt freshness; check LastReceivedAt age if adapter stops.'),
    'HasSample': ('xs:boolean', False, 'False means initial values are placeholders, not observations.'),
    'StaleAfterSeconds': ('xs:double', STALE_SECONDS, 'MQTT receipt freshness timeout in seconds.')
}

def path_for(collection, identifier):
    encoded = base64.urlsafe_b64encode(identifier.encode()).decode().rstrip('=')
    return '/' + collection + '/' + encoded

SM_PATH = path_for('submodels', SUBMODEL_ID)
SHELL_PATH = path_for('shells', SHELL_ID)

def models():
    elements = []
    for name, (kind, initial, description) in FIELDS.items():
        value = str(initial).lower() if isinstance(initial, bool) else str(initial)
        elements.append({'modelType': 'Property', 'idShort': name, 'valueType': kind,
                         'value': value, 'description': [{'language': 'en', 'text': description}]})
    submodel = {'modelType': 'Submodel', 'id': SUBMODEL_ID, 'idShort': 'HardwareTelemetry',
                'submodelElements': elements}
    shell = {'modelType': 'AssetAdministrationShell', 'id': SHELL_ID, 'idShort': 'MotorCHardwareAAS',
             'assetInformation': {'assetKind': 'Instance', 'globalAssetId': ASSET_ID},
             'submodels': [{'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': SUBMODEL_ID}]}]}
    return {'submodels': submodel, 'shells': shell}

class Evidence:
    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def event(self, filename, event):
        record = {'utc': utc_now(), 'mono': time.monotonic(), **event}
        with self.lock, (self.output/filename).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')

    def save(self, name, data):
        (self.output/name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

class API:
    def __init__(self, evidence, base='http://127.0.0.1:8082', timeout=4):
        url = urllib.parse.urlsplit(base)
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.path or url.query or url.username:
            raise ValueError('C milestone requires explicit loopback HTTP endpoint')
        self.base, self.timeout, self.evidence = base, timeout, evidence
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, payload=None):
        if method == 'PATCH':
            if path != SM_PATH+'/$value' or set(payload) != set(FIELDS):
                raise ValueError('Out-of-scope C PATCH blocked')
        elif method == 'POST':
            expected = models().get(path.lstrip('/'))
            if not expected or payload != expected:
                raise ValueError('Out-of-scope POST blocked')
        elif method != 'GET':
            raise ValueError('Unsupported method')
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Expected relative API path')
        started = time.monotonic()
        record = {'method': method, 'path': path, 'request': payload}
        try:
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(self.base+path, data=data, method=method,
                                         headers={'Content-Type': 'application/json'})
            try:
                response = self.opener.open(req, timeout=self.timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read().decode('utf-8')
                status = response.status
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = raw
            record.update(status=status, response=body)
            return status, body
        except Exception as error:
            record['error'] = repr(error)
            raise
        finally:
            record['elapsed_s'] = time.monotonic()-started
            self.evidence.event('http.jsonl', record)

    def get(self, path):
        status, body = self.request('GET', path)
        if status != 200:
            raise RuntimeError('GET failed '+str(status)+' '+path)
        return body

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def verify_metadata(model):
    expected = models()['submodels']
    if any(model.get(k) != expected[k] for k in ('id', 'idShort', 'modelType')):
        raise ValueError('C Submodel identity differs')
    elements = {p['idShort']: p for p in model['submodelElements']}
    if len(model['submodelElements']) != len(FIELDS) or set(elements) != set(FIELDS):
        raise ValueError('C Property set differs')
    for wanted in expected['submodelElements']:
        actual = elements[wanted['idShort']]
        if any(actual.get(k) != wanted[k] for k in ('modelType', 'valueType', 'description')):
            raise ValueError('Metadata differs: '+wanted['idShort'])

def verify_shell(shell):
    expected = models()['shells']
    if (any(shell.get(k) != expected[k] for k in expected if k != 'submodels')
            or any(ref not in shell.get('submodels', []) for ref in expected['submodels'])):
        raise ValueError('C shell identity or link differs')

def bootstrap(api):
    desired = models()
    preflight = {}
    # Inspect BOTH resources and their metadata before any POST.
    for collection, model in desired.items():
        status, body = api.request('GET', path_for(collection, model['id']))
        if status not in (200, 404):
            raise RuntimeError('Preflight HTTP '+str(status))
        if status == 200:
            (verify_metadata if collection == 'submodels' else verify_shell)(body)
        preflight[collection] = {'status': status, 'response': body}
    api.evidence.save('preflight.json', preflight)
    for collection, model in desired.items():
        if preflight[collection]['status'] == 404:
            status, _ = api.request('POST', '/'+collection, model)
            if status not in (201, 409):
                raise RuntimeError('POST failed '+str(status))
        current = api.get(path_for(collection, model['id']))
        (verify_metadata if collection == 'submodels' else verify_shell)(current)
    print('C model ready: '+SUBMODEL_ID, flush=True)

def values_for(state):
    sample = state['sample']
    if not sample:
        return {name: initial for name, (_, initial, _) in FIELDS.items()}
    t = sample['telemetry']
    return dict(SourceKind=sample['source_kind'], ControllerState=t['controller_state'],
                Direction=t['direction'], CommandedSteps=t['commanded_steps'],
                RemainingSteps=t['remaining_steps'], CoilsEnergized=t['coils_energized'],
                Reason=t['reason'], Sequence=t['sequence'], UptimeMs=t['uptime_ms'],
                BridgeRunId=sample['bridge_run_id'], BridgeIndex=sample['bridge_index'],
                PCReceivedAt=sample['pc_received_at'], LastReceivedAt=state['last_received_at'],
                Freshness=state['freshness'], HasSample=True, StaleAfterSeconds=STALE_SECONDS)


def verify_values(model, expected):
    verify_metadata(model)
    observed = {p['idShort']: p['value'] for p in model['submodelElements']}
    for name, wanted in expected.items():
        actual = observed[name]
        kind = FIELDS[name][0]
        if kind == 'xs:dateTime':
            matched = abs(timestamp(actual)-timestamp(wanted)) <= .000001
        elif kind == 'xs:double':
            matched = math.isfinite(float(actual)) and math.isclose(float(actual), wanted, rel_tol=0, abs_tol=1e-9)
        elif kind == 'xs:integer':
            matched = str(actual) == str(wanted)
        elif kind == 'xs:boolean':
            matched = str(actual).lower() == str(wanted).lower()
        else:
            matched = actual == wanted
        if not matched:
            raise ValueError('C readback mismatch: '+name)
    return observed

def new_evidence(prefix):
    return Evidence(ROOT/'evidence'/(prefix+'-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]))

