// Write <clip dir>/frames.ffconcat: every screencast frame held until the next one's timestamp.
import fs from 'node:fs';
import path from 'node:path';

const dir = process.argv[2];
const { frames } = JSON.parse(fs.readFileSync(path.join(dir, 'frames.json'), 'utf8'));
const q = (p) => `'${p.replace(/'/g, "'\\''")}'`;
const lines = ['ffconcat version 1.0'];
frames.forEach((f, i) => {
  const next = frames[i + 1];
  const d = next ? Math.max(0.001, next.t - f.t) : 1.0;
  lines.push(`file ${q(path.join(dir, f.file))}`, `duration ${d.toFixed(4)}`);
});
// The concat demuxer ignores the last entry's duration unless the file is listed once more.
lines.push(`file ${q(path.join(dir, frames.at(-1).file))}`);
fs.writeFileSync(path.join(dir, 'frames.ffconcat'), lines.join('\n') + '\n');
