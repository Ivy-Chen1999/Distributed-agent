// Drop the per-run database created by the webServer command (runs before the server stops,
// so connections are closed with DROP DATABASE ... WITH (FORCE)).
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export default function globalTeardown(): void {
  const url = process.env.WOMM_E2E_DATABASE_URL;
  if (!url || process.env.WOMM_E2E_KEEP_DB === '1') return;
  execFileSync('uv', ['run', 'python', '-m', 'womm.api.e2e', 'drop-db'], {
    cwd: fileURLToPath(new URL('../..', import.meta.url)),
    env: { ...process.env, DATABASE_URL: url },
    stdio: 'inherit',
  });
}
