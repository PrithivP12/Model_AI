"""Build a small allowlisted developer handoff without changing/staging Git.

Research archives, private operator resources and local credentials remain in
place. Scan reports contain only file names and findings, never secret values.
No redistribution license is invented.
"""
from pathlib import Path
import argparse,hashlib,json,re,shutil,subprocess

ROOT=Path(__file__).resolve().parents[1]
DOCS=['DIRECT_INTEGRATION.md','DIRECT_INTEGRATION_TESTS.md','LICENSE','THIRD_PARTY_NOTICES.md','PUBLICATION_STATUS.md','README.md','FRONTEND_INTEGRATION.md','API_REFERENCE.md','CONFIGURATION.md','DATA_MANIFEST.md',
      'GIT_PUBLICATION_CHECKLIST.md','LICENSE_STATUS.md','KNOWN_LIMITATIONS.md','AGROSIM_V1_RELEASE_READINESS.md',
      'FINAL20_PHYSIOLOGY.md','PLATFORM_VERIFICATION.md','FINANCE.md','pyproject.toml','requirements-release.lock',
      '.env.example','.gitignore','.dockerignore','Dockerfile','compose.yaml']
PATTERNS=[('private_key',re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
          ('jwt',re.compile(rb'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}')),
          ('github_token',re.compile(rb'gh[pousr]_[A-Za-z0-9]{30,}')),
          ('credential_assignment',re.compile(rb'(?i)(?:api[_-]?key|password|secret|token)\s*[:=]\s*[\"\x27]([A-Za-z0-9_-]{24,})[\"\x27]'))]

def scan(paths):
    findings=[];big=[];local=[]
    for p in paths:
        data=p.read_bytes()
        if len(data)>10_000_000:big.append(str(p.relative_to(ROOT)))
        for name,pattern in PATTERNS:
            if pattern.search(data):findings.append({'file':str(p.relative_to(ROOT)),'type':name})
        if b'/Users/'in data or b'/home/'in data:local.append(str(p.relative_to(ROOT)))
    return {'possible_secrets':findings,'over_10MB':big,'private_path_references':local}

def build(output):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Fresh handoff output directory required')
    paths=[]
    for folder in ['agrosim','fieldshift']:
        for p in sorted((ROOT/folder).rglob('*')):
            if p.is_file()and '__pycache__'not in p.parts and p.suffix in ['.py','.json','.js','.css','.html']:
                paths.append(p)
    for name in DOCS:
        p=ROOT/name
        if not p.is_file():raise ValueError('Missing release artifact '+name)
        paths.append(p)
    for folder in ['handoff','examples/api']:
        paths.extend(p for p in sorted((ROOT/folder).rglob('*'))if p.is_file()and 'node_modules'not in p.parts and 'dist'not in p.parts)
    for name in ['test_api_release.py','test_direct.py']:
        paths.append(ROOT/'tests'/name)
    for name in ['build_release_handoff.py','release_smoke.py']:
        paths.append(ROOT/'scripts'/name)
    report=scan(paths)
    if report['possible_secrets']or report['over_10MB']:raise ValueError('Candidate handoff requires security review; see scan results without copying secrets')
    output.mkdir(parents=True)
    for p in paths:
        dest=output/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    manifest={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest()for p in sorted(output.rglob('*'))if p.is_file()}
    report.update(files=len(manifest),bytes=sum(p.stat().st_size for p in output.rglob('*')if p.is_file()),
        included_sha256=manifest,publication_status='MIT_project_code_third_party_terms_retained',
        excluded=['research reports/raw datasets','virtualenvs/caches','credentials/private operator resources','.git history','run databases/results'],
        license='MIT project code; see LICENSE and THIRD_PARTY_NOTICES.md')
    (output/'HANDOFF_MANIFEST.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);args=p.parse_args()
    r=build(args.output);print(json.dumps({k:v for k,v in r.items()if k!='included_sha256'}))
