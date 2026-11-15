"""SQLite-backed single-host queue, one locked scheduler and one child process.

No Python threads mutate global scientific dispatch concurrently. A killed child
never publishes a completed result. Outputs are addressed only by random UUIDs.
"""
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
import fcntl,json,os,re,sqlite3,subprocess,sys,threading,time,uuid

from .models import Job,Issue
from .service import canonical,digest,ServiceError,write_atomic

def now():return datetime.now(timezone.utc).isoformat()
VALID_ID=re.compile(r'^[a-f0-9]{32}$')

class JobStore:
    def __init__(self,settings):
        self.settings=settings;self.root=settings.data_dir;self.root.mkdir(parents=True,exist_ok=True)
        self.root.chmod(0o700);(self.root/'runs').mkdir(exist_ok=True)
        self.db=self.root/'jobs.sqlite3'
        with self.connection()as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,kind TEXT,status TEXT,created TEXT,updated TEXT,input TEXT,hash TEXT,idempotency TEXT UNIQUE,cancel INTEGER DEFAULT 0,error TEXT)')
        self.db.chmod(0o600);self.stop_event=threading.Event();self.thread=None;self.process=None;self.lock=None

    @contextmanager
    def connection(self):
        con=sqlite3.connect(self.db,timeout=10);con.row_factory=sqlite3.Row
        try:yield con;con.commit()
        except:con.rollback();raise
        finally:con.close()

    def directory(self,run_id):
        if not VALID_ID.fullmatch(run_id):raise ServiceError('run_not_found','Run ID not found','run_id')
        path=self.root/'runs'/run_id
        if path.is_symlink():raise ServiceError('unsafe_run','Run storage unavailable','run_id')
        return path

    def start(self):
        self.lock=(self.root/'worker.lock').open('a')
        try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:self.lock.close();self.lock=None;raise RuntimeError('Job directory already has an active worker; use one API process')
        with self.connection()as con:
            con.execute('UPDATE jobs SET status=?,updated=?,error=? WHERE status=?',('failed',now(),json.dumps({'field':'job','code':'worker_interrupted','message':'Previous worker stopped during execution; submit a new run'}),'running'))
        self.thread=threading.Thread(target=self._loop,name='agrosim-worker',daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.process and self.process.poll()is None:self.process.terminate()
        if self.thread:self.thread.join(timeout=10)
        if self.lock:fcntl.flock(self.lock,fcntl.LOCK_UN);self.lock.close();self.lock=None

    def submit(self,kind,value,idempotency=None):
        encoded=canonical(value).decode();h=digest({'kind':kind,'request':value})
        if idempotency and(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',idempotency)):raise ServiceError('invalid_idempotency','Idempotency-Key must be 1–80 letters/numbers/_/-','Idempotency-Key')
        identity=uuid.uuid4().hex;stamp=now()
        with self.connection()as con:
            con.execute('BEGIN IMMEDIATE')
            if idempotency:
                old=con.execute('SELECT * FROM jobs WHERE idempotency=?',(idempotency,)).fetchone()
                if old:
                    if old['hash']!=h:raise ServiceError('idempotency_conflict','Key was already used for different inputs','Idempotency-Key')
                    return self._job(old)
            if con.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]>=self.settings.max_pending:
                raise ServiceError('queue_full','Simulation queue is full; retry after jobs finish','job')
            if con.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]>=self.settings.max_stored:
                raise ServiceError('retention_limit','Stored-run limit reached; operator must archive/remove terminal runs','job')
            directory=self.directory(identity);directory.mkdir(mode=0o700)
            write_atomic(directory/'input.json',value)
            con.execute('INSERT INTO jobs (id,kind,status,created,updated,input,hash,idempotency) VALUES (?,?,?,?,?,?,?,?)',
                (identity,kind,'queued',stamp,stamp,encoded,h,idempotency))
        return self.get(identity)

    def _job(self,row):
        return Job(run_id=row['id'],kind=row['kind'],status=row['status'],created_at=row['created'],updated_at=row['updated'],
            error=Issue(**json.loads(row['error']))if row['error']else None,result_url=f'/api/v1/simulations/{row["id"]}/results'if row['status']=='completed'else None)

    def get(self,identity):
        self.directory(identity)
        with self.connection()as con:row=con.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
        if row is None:raise ServiceError('run_not_found','Run ID not found','run_id')
        return self._job(row)

    def cancel(self,identity):
        job=self.get(identity)
        with self.connection()as con:
            if job.status=='queued':con.execute('UPDATE jobs SET status=?,updated=?,cancel=1 WHERE id=?',('canceled',now(),identity))
            elif job.status=='running':con.execute('UPDATE jobs SET cancel=1,updated=? WHERE id=?',(now(),identity))
        return self.get(identity)

    def get_results(self,identity):
        job=self.get(identity)
        if job.status!='completed':raise ServiceError('result_unavailable','Run has no completed result','run_id')
        return json.loads((self.directory(identity)/'result.json').read_text())

    def _loop(self):
        while not self.stop_event.wait(.1):
            with self.connection()as con:
                con.execute('BEGIN IMMEDIATE');row=con.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if not row:continue
                con.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',('running',now(),row['id']))
            identity=row['id'];directory=self.directory(identity);error=None;status='failed'
            try:
                env=os.environ.copy()
                env['AGROSIM_DATA_DIR']=str(self.root)
                if self.settings.resources_dir:env['AGROSIM_RESOURCES_DIR']=str(self.settings.resources_dir)
                # Private service token is never needed by the numerical worker.
                env.pop('AGROSIM_API_TOKEN',None)
                with (directory/'worker.log').open('wb')as log:
                    self.process=subprocess.Popen([sys.executable,'-m','agrosim.api.worker','--run-id',identity,'--kind',row['kind'],'--created-at',row['created']],
                        stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,env=env)
                    started=time.monotonic();canceled=False;timed_out=False
                    while self.process.poll()is None:
                        with self.connection()as con:cancel=con.execute('SELECT cancel FROM jobs WHERE id=?',(identity,)).fetchone()[0]
                        canceled=bool(cancel)or self.stop_event.is_set();timed_out=time.monotonic()-started>self.settings.job_timeout_seconds
                        if canceled or timed_out:
                            self.process.terminate()
                            try:self.process.wait(timeout=3)
                            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)
                            break
                        self.stop_event.wait(.1)
                    if canceled:status='canceled'
                    elif timed_out:error={'field':'job','code':'execution_timeout','message':'Configured execution time limit exceeded'}
                    elif self.process.returncode==0 and(directory/'result.json').is_file():status='completed'
                    else:
                        error={'field':'job','code':'simulation_failed','message':'Simulation did not complete; no results were fabricated'}
                        if(directory/'error.json').is_file():error=json.loads((directory/'error.json').read_text())
                if status!='completed':
                    # Preserve partial research artifacts locally, but never serve
                    # an atomically written result from a canceled/failed job.
                    if(directory/'result.json').exists():(directory/'result.json').rename(directory/'unpublished-result.json')
            except Exception:error={'field':'job','code':'worker_failure','message':'Local worker could not complete execution'}
            finally:self.process=None
            with self.connection()as con:con.execute('UPDATE jobs SET status=?,updated=?,error=? WHERE id=?',
                (status,now(),json.dumps(error)if error else None,identity))
