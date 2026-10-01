// WOMM demo stage: a scripted timeline over console screenshots captured by e2e/demo.spec.ts.
// window.__seek(t) renders time t deterministically; the renderer screenshots every frame.
// Open stage.html?assets=<dir with shots.js> in a browser to preview (it autoplays).
'use strict';

const W = 1920;
const H = 1080;
const FADE = 0.45; // crossfade between segments (s)
const VIEW = [0, 0, 1600, 900]; // the console viewport the shots were taken at (CSS px)

// One entry per beat. `cam` is where the camera ends up (a box key from shots.json, a rect or
// 'full'); it starts from the previous beat's camera when the shot is the same, so consecutive
// beats on one screen read as a pan or zoom. `ring` outlines a component once the camera settles.
const replayBeats = Array.from({ length: 16 }, (_, i) => ({
  shot: `replay-${String(i).padStart(2, '0')}`,
  dur: 0.95,
  fade: 0.35,
  cam: 'full',
  caption:
    i < 5 ? 'Replaying the real 6-minute run at 8×: the planner reads the diff, the router scores each expert.'
    : i < 10 ? 'Legal, Fiscal and Stakeholder experts run in parallel, each an isolated Claude call.'
    : 'Findings land on one shared impact board. Every quote is checked word for word against the source.',
}));

const TIMELINE = [
  { kind: 'title', dur: 4.5, kicker: 'Multi-agent regulatory impact assessment', title: 'WOMM', sub: 'Reads a change to the EU AI Act and writes an impact dossier in which every claim traces back to a quote in the law.', meta: 'A real run · real Claude output · v0' },
  { shot: 'overview', dur: 3.4, cam: 'full', caption: 'The console, showing the latest real run.' },
  { shot: 'overview', dur: 4.0, cam: 'tiles', ring: 'tiles', caption: 'At a glance: status, wall-clock time, tokens, cost and grounding. 70 of 71 quotes were found verbatim in the source.' },
  { shot: 'overview', dur: 3.6, cam: 'latency', ring: 'latency', caption: 'How long each agent took. The three experts run in parallel.' },
  { shot: 'overview', dur: 3.6, cam: 'review', ring: 'review', caption: 'What an analyst should look at: disagreements, open questions, failed experts.' },
  { shot: 'pipeline', dur: 4.2, cam: 'graph', ring: 'graph', caption: 'The pipeline: diff → Impact Planner → router (Jev) → three experts → impact board → citation check → synthesis → dossier.' },
  ...replayBeats,
  { shot: 'pipeline', dur: 3.8, cam: 'node', ring: 'node', caption: 'Every node records its backend, model, prompt hash, tokens and latency.' },
  { shot: 'detail', dur: 3.2, cam: 'head', ring: 'head', caption: 'The Impact Dossier: 30 impacts, open questions and disagreements.' },
  { shot: 'detail-open', dur: 4.8, cam: 'impact', ring: 'impact', caption: 'Each impact merges findings from several experts. Every finding traces back: change → affected actor → mechanism → evidence quote → source.' },
  { shot: 'source', dur: 4.2, cam: 'panel', ring: 'mark', caption: 'One click shows the quote highlighted in the original legal text.' },
  { shot: 'disagreements', dur: 3.6, cam: 'body', ring: 'body', caption: 'Disagreements between experts are kept, not averaged away.' },
  { shot: 'questions', dur: 3.6, cam: 'body', ring: 'body', caption: 'Open questions for an analyst, including evidence that could not be verified.' },
  { shot: 'agents', dur: 3.6, cam: 'cards', caption: 'One card per agent: prompt file and hash, model, latency, tokens and cost.' },
  { shot: 'topology', dur: 4.0, cam: 'graph', ring: 'graph', caption: 'How data flows between agents. Dashed boxes are isolated `claude -p` subprocesses.' },
  { shot: 'ask-before', dur: 2.8, cam: 'full', ring: 'input', caption: 'Ask WOMM answers questions from this run’s dossier only.' },
  { shot: 'ask-after', dur: 5.2, cam: 'answer', ring: 'answer', caption: 'A real answer from Claude, citing the impacts it is based on.' },
  { shot: 'settings', dur: 3.6, cam: 'full', caption: 'Settings: the content-addressed system version, a backend per role, the router mode and the isolation self-check.' },
  { shot: 'overview-dark', dur: 3.4, cam: 'full', caption: 'Dark theme. Every screen passes an automated WCAG 2.1 AA check.' },
  { kind: 'phones', dur: 4.4, shots: ['phone-overview', 'phone-pipeline', 'phone-topology'], caption: 'Responsive down to 390 px phones.' },
  { kind: 'title', dur: 4.6, kicker: 'WOMM v0', title: 'Every impact traces back\nto the law.', sub: 'LangGraph · Pydantic · LangSmith · Jev · Railway', meta: 'github.com/Ivy-Chen1999/Distributed-agent' },
];

const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const clamp01 = (t) => Math.max(0, Math.min(1, t));

/** Expand a component box to a 16:9 camera rect with padding, no more than 2x zoom. */
function camRect(shot, cam) {
  if (cam === 'full') return VIEW;
  const box = Array.isArray(cam) ? cam : shot.boxes[cam];
  if (!box) return VIEW;
  const pad = 36;
  let [x, y, w, h] = [box[0] - pad, box[1] - pad, box[2] + 2 * pad, box[3] + 2 * pad];
  const minW = W / 2; // the shots have 2x pixels, so up to 2x zoom stays sharp
  if (w < minW) { x -= (minW - w) / 2; w = minW; }
  const aspect = W / H;
  if (w / h < aspect) { const nw = h * aspect; x -= (nw - w) / 2; w = nw; } else { const nh = w / aspect; y -= (nh - h) / 2; h = nh; }
  if (w <= shot.w) x = Math.max(0, Math.min(x, shot.w - w));
  if (h <= shot.h) y = Math.max(0, Math.min(y, shot.h - h));
  return [x, y, w, h];
}

const lerp = (a, b, t) => a + (b - a) * t;
const lerpRect = (a, b, t) => a.map((v, i) => lerp(v, b[i], t));

function el(tag, cls, parent) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (parent) parent.appendChild(e);
  return e;
}

async function init() {
  const params = new URLSearchParams(location.search);
  const dir = params.get('assets');
  // shots.js sets window.SHOTS (a script tag works from file://, fetch does not).
  await new Promise((resolve, reject) => {
    const s = el('script', '', document.head);
    s.onload = resolve;
    s.onerror = () => reject(new Error(`cannot load ${dir}/shots.js`));
    s.src = `file://${dir}/shots.js`;
  });
  const shots = window.SHOTS;
  const stage = document.getElementById('stage');
  const caption = document.getElementById('caption');
  const progress = document.getElementById('progress');
  const decodes = [];

  let t0 = 0;
  let prev = null;
  const beats = TIMELINE.map((b) => {
    const layer = el('div', 'layer');
    stage.insertBefore(layer, caption);
    const beat = { ...b, start: t0, end: t0 + b.dur, fade: b.fade ?? FADE, layer };
    if (b.kind === 'title') {
      const card = el('div', 'card', layer);
      el('div', 'kicker', card).textContent = b.kicker;
      const title = el('div', 'title', card);
      title.style.whiteSpace = 'pre-line';
      title.textContent = b.title;
      el('div', 'rule', card);
      el('div', 'sub', card).textContent = b.sub;
      el('div', 'meta', card).textContent = b.meta;
    } else if (b.kind === 'phones') {
      const row = el('div', 'phones', layer);
      for (const name of b.shots) {
        const img = el('img', '', row);
        img.src = `file://${dir}/${shots[name].file}`;
        decodes.push(img.decode());
      }
    } else {
      const shot = shots[b.shot];
      const img = el('img', 'shot', layer);
      img.src = `file://${dir}/${shot.file}`;
      img.style.width = `${shot.w}px`;
      img.style.height = `${shot.h}px`;
      decodes.push(img.decode());
      beat.img = img;
      beat.shotData = shot;
      beat.to = camRect(shot, b.cam);
      beat.from = prev && prev.shot === b.shot && prev.to ? prev.to : b.cam === 'full' || b.shot.startsWith('replay') ? beat.to : VIEW;
      if (b.ring && shot.boxes[b.ring]) {
        beat.ringBox = shot.boxes[b.ring];
        beat.ring = el('div', 'ring', layer);
      }
    }
    t0 += b.dur;
    prev = beat;
    return beat;
  });
  const duration = t0;
  await Promise.all(decodes);
  await document.fonts.ready;

  function render(t) {
    let text = '';
    let textAlpha = 0;
    beats.forEach((b, i) => {
      const next = beats[i + 1];
      // A beat fades in over the previous one's tail and stays until the next has fully faded in.
      const alive = t >= b.start - (i === 0 ? 0 : b.fade) && t < (next ? next.start : Infinity);
      b.layer.style.display = alive ? 'block' : 'none';
      if (!alive) return;
      b.layer.style.opacity = String(i === 0 ? 1 : clamp01((t - (b.start - b.fade)) / b.fade));
      b.layer.style.zIndex = String(i);
      const local = clamp01((t - b.start) / b.dur);
      if (b.img) {
        // Move during the first 55% of the beat, then hold.
        const k = ease(clamp01(local / 0.55));
        const [x, y, w] = lerpRect(b.from, b.to, k);
        const s = W / w;
        b.img.style.transform = `scale(${s}) translate(${-x}px, ${-y}px)`;
        if (b.ring) {
          const [bx, by, bw, bh] = b.ringBox;
          const r = 10;
          Object.assign(b.ring.style, {
            left: `${(bx - x - r) * s}px`, top: `${(by - y - r) * s}px`, width: `${(bw + 2 * r) * s}px`, height: `${(bh + 2 * r) * s}px`,
            opacity: String(clamp01((local - 0.5) / 0.15) * clamp01((b.dur - (t - b.start)) / 0.35)),
          });
        }
      }
      if (t >= b.start && t < b.end && b.caption) {
        text = b.caption;
        const sameBefore = beats[i - 1]?.caption === b.caption;
        const sameAfter = next?.caption === b.caption;
        const into = t - b.start;
        const left = b.end - t;
        textAlpha = Math.min(sameBefore ? 1 : clamp01(into / 0.3), sameAfter ? 1 : clamp01(left / 0.3));
      }
    });
    caption.textContent = text;
    caption.style.display = text ? 'block' : 'none';
    caption.style.opacity = String(textAlpha);
    progress.style.width = `${(t / duration) * 100}%`;
  }

  window.__duration = duration;
  window.__seek = async (t) => {
    render(t);
  };
  window.__ready = true;
  if (!navigator.webdriver) {
    const startAt = performance.now();
    const loop = () => {
      render(((performance.now() - startAt) / 1000) % duration);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  } else {
    render(0);
  }
}

init();
