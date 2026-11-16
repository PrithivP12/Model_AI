/** Server-side Node adapter. No network, API URL, shell, or daemon. */
import {spawn} from 'node:child_process';

export function runAgroSim(request, {command = 'simulate', python = 'python3', timeoutMs = 300000} = {}) {
  if (!['simulate', 'compare', 'validate', 'crops'].includes(command)) throw new Error('Unsupported command');
  const body = JSON.stringify(request ?? {});
  if (Buffer.byteLength(body) > 2000000) throw new Error('Request exceeds 2000000 bytes');
  return new Promise((resolve, reject) => {
    const child = spawn(python, ['-m', 'agrosim.direct', command], {stdio: ['pipe', 'pipe', 'pipe'], shell: false});
    let stdout = '', stderr = '', bytes = 0, failure;
    const timer = setTimeout(() => {failure = new Error('AgroSim execution timed out'); child.kill('SIGKILL');}, timeoutMs);
    child.on('error', error => {clearTimeout(timer); reject(error);});
    child.stdin.on('error', error => {if (error.code !== 'EPIPE') failure = error;});
    for (const [stream, kind] of [[child.stdout, 'out'], [child.stderr, 'err']]) {
      stream.setEncoding('utf8');
      stream.on('data', text => {
        bytes += Buffer.byteLength(text);
        if (bytes > 32000000) {failure = new Error('AgroSim output exceeds limit'); child.kill('SIGKILL'); return;}
        if (kind === 'out') stdout += text; else stderr += text;
      });
    }
    child.on('close', code => {
      clearTimeout(timer);
      if (failure) return reject(failure);
      if (code !== 0) {
        const error = new Error('AgroSim execution failed');
        try {error.details = JSON.parse(stderr);} catch {error.details = {errors: [{code: 'worker_failed', message: 'Check Python dependencies and request inputs'}]};}
        return reject(error);
      }
      try {resolve(JSON.parse(stdout));} catch {reject(new Error('Invalid AgroSim worker response'));}
    });
    child.stdin.end(body);
  });
}
