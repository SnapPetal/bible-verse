import { execFileSync } from 'node:child_process';
import * as path from 'node:path';

test('the complete infrastructure project passes the pinned TypeScript compiler', () => {
  execFileSync(process.execPath, [require.resolve('typescript/bin/tsc'), '--noEmit'], {
    cwd: path.resolve(__dirname, '..'),
    stdio: 'pipe',
    timeout: 120000,
  });
}, 125000);
