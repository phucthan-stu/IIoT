"""Workspace orchestration only. No changes to Duong's adapter or historical evidence."""
import asyncio, ctypes, hashlib, json, msvcrt, os, runpy, socket, subprocess, sys, threading, time, uuid
from ctypes import wintypes as W
from pathlib import Path
ROOT = Path(__file__).resolve().parent
C = json.loads((ROOT/'project.json').read_text(encoding='utf-8'))
STATE = ROOT/'state'
SERVICES = ('a', 'b', 'sync', 'recovery')
STATE.mkdir(exist_ok=True)
sys.dont_write_bytecode = True
sys.path.append(C['borrow'])
sys.path.insert(0, C['source'])
os.environ.update(AAS_BASE_URL='http://127.0.0.1:8082', AAS_SECURITY_MODE='local', POLL_PERIOD_S='1.0', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
for key in ('AAS_TOKEN_FILE', 'AAS_CA_BUNDLE', 'PYTHONPATH'): os.environ.pop(key, None)

class Lock:
    def __init__(self, name):
        self.f = (STATE/(name+'.lock')).open('a+b')
        if self.f.tell()==0: self.f.write(b'0'); self.f.flush()
        self.f.seek(0)
        try: msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.f.close(); raise RuntimeError('Task already running / busy: '+name)
    def close(self):
        self.f.seek(0); msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1); self.f.close()

def busy(name):
    try: lock = Lock(name)
    except RuntimeError: return True
    lock.close(); return False

# A Windows Job owns ONLY this launch and its descendants. Kernel cleanup also
# works if the VS Code task/terminal is killed. No PID-based global termination.
K = ctypes.WinDLL('kernel32', use_last_error=True)
class BASIC(ctypes.Structure):
    _fields_=[('ProcessTime',ctypes.c_int64),('JobTime',ctypes.c_int64),('Flags',W.DWORD),('Min',ctypes.c_size_t),('Max',ctypes.c_size_t),('Active',W.DWORD),('Affinity',ctypes.c_size_t),('Priority',W.DWORD),('Scheduling',W.DWORD)]
class IO(ctypes.Structure):
    _fields_=[(x,ctypes.c_uint64) for x in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
class EXT(ctypes.Structure):
    _fields_=[('Basic',BASIC),('Io',IO),('ProcessMemory',ctypes.c_size_t),('JobMemory',ctypes.c_size_t),('PeakProcess',ctypes.c_size_t),('PeakJob',ctypes.c_size_t)]
K.CreateJobObjectW.argtypes=[ctypes.c_void_p,W.LPCWSTR]; K.CreateJobObjectW.restype=W.HANDLE
K.SetInformationJobObject.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD]
K.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE]
K.CloseHandle.argtypes=[W.HANDLE]
class Job:
    def __init__(self):
        self.h=K.CreateJobObjectW(None,None)
        if not self.h: raise ctypes.WinError(ctypes.get_last_error())
        info=EXT(); info.Basic.Flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not K.SetInformationJobObject(self.h,9,ctypes.byref(info),ctypes.sizeof(info)):
            self.close(); raise ctypes.WinError(ctypes.get_last_error())
    def assign(self,p):
        if not K.AssignProcessToJobObject(self.h,W.HANDLE(int(p._handle))):
            p.kill(); p.wait(); raise ctypes.WinError(ctypes.get_last_error())
    def close(self):
        if self.h: K.CloseHandle(self.h); self.h=None

def get(url):
    import requests
    with requests.Session() as s:
        s.trust_env=False
        r=s.get(url,timeout=4,allow_redirects=False); r.raise_for_status(); return r

def wait(label, fn, seconds=45):
    until=time.monotonic()+seconds; last=None
    while time.monotonic()<until:
        try:
            result=fn()
            if result is not False: return result
        except Exception as e: last=str(e)
        time.sleep(.5)
    raise RuntimeError(label+' not ready: '+str(last))

def paths():
    for name,path in C.items():
        if not Path(path).exists(): raise RuntimeError('Missing '+name+': '+path)
    for name in ('docker-compose.yml','docker-compose.override.yml','basyx-infra.yml'):
        assert (Path(C['basyx'])/name).is_file(), name
    assert Path(sys.executable).resolve()==Path(C['python']).resolve(), 'Wrong Python'
    hashes=json.loads((Path(C['evidence'])/'sync-duong-2026-11-17/source-hashes.json').read_text())
    for name,h in hashes.items():
        assert hashlib.sha256((Path(C['source'])/name).read_bytes()).hexdigest()==h, 'Source changed: '+name
    import requests,pymodbus,asyncua
    actual=(requests.__version__,pymodbus.__version__,asyncua.__version__)
    assert actual==('2.32.3','3.11.0','1.1.8'), actual
    print('Python:',sys.version.split()[0],sys.executable,'; libraries:',actual,flush=True)
    print('Paths and original source hashes OK.',flush=True)

def docker(*args):
    r=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=30)
    if r.returncode: raise RuntimeError('Docker unavailable / command failed. Open Docker Desktop and wait for Engine ready.\n'+r.stderr)
    return r.stdout

def containers():
    expected=json.loads(Path(C['runtime']).read_text(encoding='utf-8-sig'))['Containers']
    result={}
    for old in expected:
        obj=json.loads(docker('inspect',old['Name']))[0]
        assert obj['Id']==old['ContainerId'] and obj['Image']==old['ImageId'], 'Container/image differs: '+old['Name']
        assert obj['Mounts']==old['Mounts'], 'Mount differs: '+old['Name']
        result[old['Name']]=obj
    return result

def models():
    import aas_sync
    for m in ('A','B'):
        model=get('http://127.0.0.1:8082/submodels/'+aas_sync.encoded_submodel_id(m)).json()
        assert model['id']==f'urn:university:aas-demo:submodel:motor-{m.lower()}:operational'
        fields={p['idShort']:p for p in model['submodelElements']}
        expected={'MotorTemperature','MotorSpeed','MotorCurrent','OperatingState'}
        assert set(fields)==expected, 'Unexpected properties'
        for name,p in fields.items():
            assert p['modelType']=='Property' and p['valueType']==('xs:boolean' if name=='OperatingState' else 'xs:double')
    return True

def source_ready(m):
    import aas_sync
    raw=aas_sync.read_motor_a() if m=='a' else asyncio.run(asyncio.wait_for(aas_sync.read_motor_b(),4))
    aas_sync.normalize(raw,'modbus' if m=='a' else 'opcua')
    return True

def free(port):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        s.bind(('127.0.0.1',port))

def no_external_writer():
    command = "@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and ($_.CommandLine -match 'aas_sync\\.py|run_recovery\\.py|run_trial\\.py.*sync|demo\\.py.*_child.*sync') }).Count"
    r=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,text=True,timeout=15)
    assert r.returncode==0, 'Cannot inspect existing Python processes; refusing a second writer'
    assert int(r.stdout.strip())==0, 'Another sync/recovery process is running outside this task. Inspect and stop its own terminal first.'

def prepare_recovery():
    dest=ROOT/'runs'/('recovery-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    dest.mkdir(parents=True)
    old=Path(C['evidence'])/'recovery-duong-2026-11-17'
    for name in ('run_recovery.py','controller.py','analyze_evidence.py'):
        text=(old/name).read_text(encoding='utf-8')
        if name=='run_recovery.py':
            lines=text.splitlines()
            lines=[('SOURCE=pathlib.Path('+repr(C['source'])+')') if line.startswith('SOURCE=') else ('BORROW=pathlib.Path('+repr(C['borrow'])+')') if line.startswith('BORROW=') else line for line in lines]
            text='\n'.join(lines)+'\n'
        if name=='analyze_evidence.py':
            text='\n'.join(('source=Path('+repr(C['source'])+')') if line.startswith('source=Path(') else line for line in text.splitlines())+'\n'
        # Only relocate the runner output and baseline reference. Application
        # source and tested recovery logic remain unchanged.
        if name=='controller.py':
            needle="ROOT.parent/'sync-duong-2026-11-17'/'source-hashes.json'"
            assert text.count(needle)==1
            text=text.replace(needle,'pathlib.Path('+repr(str(Path(C['evidence'])/'sync-duong-2026-11-17/source-hashes.json'))+')')
        (dest/name).write_text(text,encoding='utf-8')
    (dest/'workspace-run.json').write_text(json.dumps({'status':'PREPARED','historical_runner':str(old),'source':C['source']},indent=2))
    return dest

def child(mode, target):
    if sys.stdin.readline().strip()!='GO': return
    if mode=='selftest':
        print('child stdout OK',flush=True)
        print('child stderr OK',file=sys.stderr,flush=True)
        time.sleep(60)
        return
    if mode=='recovery':
        sys.path.insert(0,str(Path(target).parent)); sys.argv=[target]
        runpy.run_path(target,run_name='__main__')
        runpy.run_path(str(Path(target).parent/'analyze_evidence.py'),run_name='__main__')
        report=json.loads((Path(target).parent/'verification.json').read_text())
        independent=json.loads((Path(target).parent/'independent-validation.json').read_text())
        assert report['passed'] and independent['all_passed'], 'Recovery FAIL; inspect run folder'
    else:
        sys.argv=[target]; runpy.run_path(target,run_name='__main__')

def forward_output(stream):
    try:
        for line in iter(stream.readline, b''):
            print(line.decode('utf-8',errors='replace'),end='',flush=True)
    finally:
        stream.close()


def launch(mode):
    gate=Lock('admission'); held=None
    try:
        assert not busy('recovery'), 'Recovery running; stop it first'
        if mode=='recovery':
            assert not any(busy(s) for s in SERVICES), 'Stop demo tasks before recovery'
        held=Lock(mode)
        stop=STATE/(mode+'.stop'); stop.unlink(missing_ok=True)
    finally: gate.close()
    job=None; proc=None; folder=None; output_thread=None
    try:
        paths()
        if mode in ('a','b'): free(15020 if mode=='a' else 4840)
        if mode in ('sync','recovery'):
            no_external_writer()
            containers(); wait('BaSyx models',models)
        if mode=='sync':
            wait('Modbus A protocol',lambda:source_ready('a'),30)
            wait('OPC UA B protocol',lambda:source_ready('b'),30)
        if mode=='recovery':
            free(15020); free(4840); folder=prepare_recovery(); target=folder/'run_recovery.py'
        else: target=Path(C['source'])/{'a':'motor_a_modbus.py','b':'motor_b_opcua.py','sync':'aas_sync.py'}[mode]
        if stop.exists(): raise RuntimeError('Cancelled before launch')
        job=Job()
        cmd=[C['python'],'-B',str(Path(__file__).resolve()),'_child',mode,str(target)]
        child_env=dict(os.environ, PYTHONIOENCODING='utf-8')
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=child_env,creationflags=subprocess.CREATE_NO_WINDOW)
        job.assign(proc); proc.stdin.write(b'GO\n'); proc.stdin.close()
        print('Started',mode,'PID',proc.pid,'; stop using task 08 or Ctrl+C.',flush=True)
        output_thread=threading.Thread(target=forward_output,args=(proc.stdout,),daemon=True)
        output_thread.start()
        if folder: print('NEW EVIDENCE:',folder,flush=True)
        while proc.poll() is None:
            if stop.exists(): print('Project stop requested.',flush=True); break
            time.sleep(.3)
        if proc.poll() is not None and proc.returncode: raise RuntimeError('Child exited: '+str(proc.returncode))
    finally:
        if job: job.close()
        if proc: proc.wait(timeout=10)
        if output_thread: output_thread.join(timeout=5)
        if folder:
            (folder/'workspace-run.json').write_text(json.dumps({'status':'FINISHED' if (folder/'independent-validation.json').exists() else 'INTERRUPTED_OR_FAILED','exit_code':proc.returncode if proc else None},indent=2))
        if held: held.close()

def stop_all():
    gate=Lock('admission')
    try:
        active=[s for s in ('sync','recovery','a','b') if busy(s)]
        for s in active: (STATE/(s+'.stop')).write_text('stop project-owned job')
    finally: gate.close()
    wait('Project tasks stopped',lambda:not any(busy(s) for s in active),70)
    print('Stopped project tasks:',active,'; BaSyx and unrelated processes untouched.')

def selftest():
    l=Lock('selftest')
    try: assert busy('selftest')
    finally: l.close()
    assert not busy('selftest')
    j=Job(); p=None
    try:
        p=subprocess.Popen([C['python'],'-B',str(Path(__file__).resolve()),'_child','selftest','unused'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        j.assign(p); p.stdin.write(b'GO\n');p.stdin.close();time.sleep(.3);assert p.poll() is None
    finally: j.close()
    p.wait(timeout=5)
    output=p.stdout.read(); p.stdout.close()
    assert b'child stdout OK' in output and b'child stderr OK' in output, output
    print('PASS: child stdout/stderr capture, duplicate lock, release, owned-process Job cleanup. No demo services started.')

def main():
    action=sys.argv[1]
    if action=='_child': return child(sys.argv[2],sys.argv[3])
    if action in SERVICES: return launch(action)
    if action=='stop': return stop_all()
    if action=='selftest': return selftest()
    paths()
    if action=='check':
        print('Docker server:',docker('version','--format','{{.Server.Version}}').strip())
        for name,c in containers().items(): print(name,c['State']['Status'])
        for port in (15020,4840,8082,3000):
            with socket.socket() as s: s.settimeout(.5); print('Port',port,'LISTEN' if s.connect_ex(('127.0.0.1',port))==0 else 'CLOSED')
        models(); print('BaSyx models OK')
    elif action=='basyx':
        lock=Lock('basyx')
        try:
            cs=containers() # Validate ALL identities before any start
            for name in ('postgres_db','aas-environment','aas-web-ui'):
                if not cs[name]['State']['Running']: print(docker('start',name))
                if name=='postgres_db':
                    wait('PostgreSQL healthy',lambda:json.loads(docker('inspect',name))[0]['State'].get('Health',{}).get('Status')=='healthy',60)
                elif name=='aas-environment': wait('AAS API',lambda:get('http://127.0.0.1:8082/submodels'))
                else: wait('AAS Viewer',lambda:get('http://127.0.0.1:3000/aasviewer'))
            print('BaSyx ready. No pull/build/recreate; configuration container untouched.')
        finally: lock.close()
    elif action=='viewer':
        get('http://127.0.0.1:3000/aasviewer'); os.startfile('http://127.0.0.1:3000/aasviewer')
    else: raise ValueError(action)

if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: print('Task interrupted; owned processes cleaned up.'); sys.exit(130)
    except Exception as e: print('STOP:',e,file=sys.stderr); sys.exit(1)
