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
STALE_SECONDS = 6.0
import hardware_model

ROOT = Path(__file__).resolve().parent
SHELL_ID = 'urn:university:aas-demo:shell:motor-c-stepper-hardware'
SUBMODEL_ID = 'urn:university:aas-demo:submodel:motor-c-stepper:environment-telemetry'
ASSET_ID = 'urn:university:aas-demo:asset:motor-c-stepper'
EPOCH = '1970-01-01T00:00:00+00:00'
# (AAS type, initial value, description)
FIELDS = {
    'SensorType': ('xs:string', 'DHT11', 'Air temperature and relative humidity sensor; not motor winding temperature.'),
    'AmbientTemperatureC': ('xs:double', 0.0, 'Last successful air temperature reading in degrees Celsius; check HasSample and Freshness.'),
    'RelativeHumidityPct': ('xs:double', 0.0, 'Last successful relative humidity reading in percent; check HasSample and Freshness.'),
    'HasSample': ('xs:boolean', False, 'False means initial numeric values are placeholders, not measurements.'),
    'LatestAttemptValid': ('xs:boolean', False, 'Validity of latest sensor attempt, not transport freshness.'),
    'Sequence': ('xs:integer', 0, 'Latest DHT attempt number; repeated motor telemetry does not increment it.'),
    'LastValidAttempt': ('xs:integer', 0, 'Attempt number corresponding to the retained successful measurements.'),
    'ReadUptimeMs': ('xs:integer', 0, 'ESP32 uptime after the latest DHT read attempt, not UTC.'),
    'LastValidReadUptimeMs': ('xs:integer', 0, 'ESP32 uptime of the retained successful measurements.'),
    'BridgeRunId': ('xs:string', '', 'Python capture ID, not sensor or ESP32 boot session ID.'),
    'LastReceivedAt': ('xs:dateTime', EPOCH, 'First MQTT acceptance UTC of last distinct successful DHT attempt; repeats do not renew it.'),
    'Freshness': ('xs:string', 'waiting', 'waiting, fresh, error or stale; evaluate LastReceivedAt age after adapter stops.'),
    'StaleAfterSeconds': ('xs:double', STALE_SECONDS, 'Sensor reading age threshold; transport silence also forces stale after 2 seconds.')
}

def path_for(collection, identifier):
    encoded = base64.urlsafe_b64encode(identifier.encode()).decode().rstrip('=')
    return '/' + collection + '/' + encoded

SM_PATH = path_for('submodels', SUBMODEL_ID)
SHELL_PATH = path_for('shells', SHELL_ID)

def environment_ref():
    return {'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': SUBMODEL_ID}]}

def models():
    elements = []
    for name, (kind, initial, description) in FIELDS.items():
        value = str(initial).lower() if isinstance(initial, bool) else str(initial)
        elements.append({'modelType': 'Property', 'idShort': name, 'valueType': kind,
                         'value': value, 'description': [{'language': 'en', 'text': description}]})
    submodel = {'modelType': 'Submodel', 'id': SUBMODEL_ID, 'idShort': 'EnvironmentTelemetry',
                'submodelElements': elements}
    shell = {'modelType': 'AssetAdministrationShell', 'id': SHELL_ID, 'idShort': 'MotorCHardwareAAS',
             'assetInformation': {'assetKind': 'Instance', 'globalAssetId': ASSET_ID},
             'submodels': hardware_model.models()['shells']['submodels'] + [environment_ref()]}
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
            expected = environment_ref() if path == SHELL_PATH+'/submodel-refs' else models().get(path.lstrip('/'))
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
    shell = api.get(SHELL_PATH)
    hardware_model.verify_shell(shell)
    status, existing = api.request('GET', SM_PATH)
    if status not in (200, 404):
        raise RuntimeError('Environment preflight HTTP ' + str(status))
    if status == 200:
        verify_metadata(existing)
    api.evidence.save('preflight.json', {'shell': shell, 'submodel_status': status, 'submodel': existing})
    if status == 404:
        code, _ = api.request('POST', '/submodels', models()['submodels'])
        if code not in (201, 409):
            raise RuntimeError('Environment POST failed: ' + str(code))
    verify_metadata(api.get(SM_PATH))
    if environment_ref() not in shell.get('submodels', []):
        code, _ = api.request('POST', SHELL_PATH+'/submodel-refs', environment_ref())
        if code not in (200, 201, 204, 409):
            raise RuntimeError('Environment link POST failed: ' + str(code))
    verify_shell(api.get(SHELL_PATH))
    print('Environment submodel ready under MotorCHardwareAAS', flush=True)


def values_for(state):
    result = {name: initial for name, (_, initial, _) in FIELDS.items()}
    result['Freshness'] = state['freshness']
    if state['sample']:
        result['BridgeRunId'] = state['sample']['bridge_run_id']
    current, good = state.get('current'), state.get('good')
    if current:
        result.update(LatestAttemptValid=current['valid'], Sequence=current['attempt'],
                      ReadUptimeMs=current['read_uptime_ms'])
    if good:
        result.update(AmbientTemperatureC=good['temperature_c'], RelativeHumidityPct=good['humidity_pct'],
                      HasSample=True, LastValidAttempt=good['attempt'],
                      LastValidReadUptimeMs=good['read_uptime_ms'], LastReceivedAt=state['last_received_at'])
    return result


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

