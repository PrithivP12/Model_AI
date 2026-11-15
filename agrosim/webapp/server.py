"""Single-origin local HTTP application. No arbitrary file, URL or solver access."""
import argparse
import gzip
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .service import DemoService,canonical

STATIC=Path(__file__).with_name('static')
ASSETS={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def respond(self,status,value,mime='application/json'):
            data=canonical(value)if mime=='application/json'else value
            self.send_response(status)
            if len(data)>2048 and 'gzip'in self.headers.get('Accept-Encoding',''):
                data=gzip.compress(data,mtime=0);self.send_header('Content-Encoding','gzip')
            self.send_header('Content-Type',mime+'; charset=utf-8');self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('Vary','Accept-Encoding')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers();self.wfile.write(data)

        def do_GET(self):
            path=urlsplit(self.path).path
            try:
                if path in ASSETS:
                    name,mime=ASSETS[path];return self.respond(200,(STATIC/name).read_bytes(),mime)
                if path=='/api/health':return self.respond(200,{'status':'ok','physical_builds':service.physical_builds,'cached_results':len(service.cache)})
                if path=='/api/demo':return self.respond(200,service.simulation({}))
                return self.respond(404,{'error':'Resource unavailable'})
            except (OSError,ValueError,KeyError,ArithmeticError):return self.respond(503,{'error':'Prepared data unavailable. Check the local data files and try again.'})

        def do_POST(self):
            # Cross-site pages must not trigger expensive local computation.
            origin=self.headers.get('Origin')
            if origin and origin!=f'http://{self.headers.get("Host")}':return self.respond(403,{'error':'Origin rejected'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.respond(415,{'error':'JSON required'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4096:raise ValueError('Request must be between 1 and 4096 bytes')
                value=json.loads(self.rfile.read(size))
                if not isinstance(value,dict):raise ValueError('Expected an object')
                path=urlsplit(self.path).path
                if path=='/api/simulate':return self.respond(200,service.simulation(value))
                if path=='/api/priorities':return self.respond(200,service.priorities(value.get('result_id'),value.get('priorities')))
                return self.respond(404,{'error':'Resource unavailable'})
            except (ValueError,KeyError,TypeError)as exc:return self.respond(422,{'error':str(exc)})
            except (OSError,ArithmeticError):return self.respond(503,{'error':'Required local inputs or conserved solution unavailable; simulation was not fabricated.'})

        def log_message(self,format,*args):
            # URLs contain no user secrets; request bodies are never logged.
            super().log_message(format,*args)
    return Handler


def main():
    parser=argparse.ArgumentParser(description='Run AgroSim locally; backend and frontend share one origin.')
    parser.add_argument('--port',type=int,default=8765);parser.add_argument('--demo',type=Path)
    args=parser.parse_args();service=DemoService(args.demo)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(service))
    print(f'AgroSim: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
