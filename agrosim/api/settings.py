from dataclasses import dataclass
from pathlib import Path
import os

@dataclass(frozen=True)
class Settings:
    data_dir:Path
    resources_dir:Path|None=None
    token:str|None=None
    origins:tuple=('http://localhost:3000','http://127.0.0.1:3000')
    hosts:tuple=('localhost','127.0.0.1','testserver')
    max_pending:int=20
    max_stored:int=500
    max_body_bytes:int=2_000_000
    job_timeout_seconds:int=300

    @classmethod
    def from_env(cls):
        def bounded(name,default,low,high):
            value=int(os.getenv(name,str(default)))
            if not low<=value<=high:raise ValueError('Invalid '+name)
            return value
        origins=tuple(x.strip()for x in os.getenv('AGROSIM_CORS_ORIGINS','http://localhost:3000,http://127.0.0.1:3000').split(',')if x.strip())
        if any(not x.startswith(('http://','https://'))or '*'in x for x in origins):raise ValueError('Explicit HTTP CORS origins required')
        token=os.getenv('AGROSIM_API_TOKEN')or None
        if token and len(token)<32:raise ValueError('API token must contain at least 32 characters')
        resources=os.getenv('AGROSIM_RESOURCES_DIR')
        return cls(Path(os.getenv('AGROSIM_DATA_DIR','var/agrosim')).resolve(),Path(resources).resolve()if resources else None,
            token,origins,tuple(os.getenv('AGROSIM_ALLOWED_HOSTS','localhost,127.0.0.1,testserver').split(',')),
            bounded('AGROSIM_MAX_PENDING',20,1,100),bounded('AGROSIM_MAX_STORED',500,1,10000),
            bounded('AGROSIM_MAX_BODY_BYTES',2_000_000,4096,5_000_000),bounded('AGROSIM_JOB_TIMEOUT_SECONDS',300,5,3600))
