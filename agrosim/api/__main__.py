import argparse
import uvicorn
from .settings import Settings
from .app import create_app

def main():
    p=argparse.ArgumentParser(description='AgroSim V1 local API, one durable worker; separate from frontend')
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8000)
    args=p.parse_args();settings=Settings.from_env()
    if args.host not in ['127.0.0.1','localhost','::1']and not settings.token:raise SystemExit('Non-loopback binding requires AGROSIM_API_TOKEN; public gateway protections still required')
    uvicorn.run(create_app(settings),host=args.host,port=args.port,workers=1,log_level='info')

if __name__=='__main__':main()
