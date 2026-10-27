// Local page of the app (#78) : polls the bridge's /state, read-only.
import { Chessground } from '/vendor/chessground.min.js';

const POLL_MS = 500;
const LIGHT_CLASS = {
  on: 'on', searching: 'warn', connecting: 'warn', reconnecting: 'warn', fixing: 'warn',
};

const cg = Chessground(document.getElementById('board'), {
  fen: 'start',
  viewOnly: true,
  coordinates: true,
  animation: { enabled: true, duration: 200 },
  drawable: { enabled: false },
});

const el = (id) => document.getElementById(id);
let lastFen = null;
let lastColor = null;

function setLight(name, value) {
  const node = el(`light-${name}`);
  node.className = `light ${LIGHT_CLASS[value] || 'waiting'}`;
}

function statusText(state) {
  if (!state.fen) return 'En attente de la partie…';
  if (state.finished) return 'Partie terminée.';
  if (!state.live) return 'Partie terminée : remettez la position finale sur l\'échiquier.';
  const turn = state.fen.split(' ')[1] === 'w' ? 'white' : 'black';
  return turn === state.color ? 'À vous de jouer.' : 'Au tour de l\'adversaire.';
}

function renderMoves(history) {
  const list = el('moves');
  const items = [];
  for (let i = 0; i < history.length; i += 2) {
    const li = document.createElement('li');
    li.textContent = history.slice(i, i + 2).join('  ');
    items.push(li);
  }
  list.replaceChildren(...items);
  list.scrollTop = list.scrollHeight;
}

function render(state) {
  el('game').textContent = `Échiquier : ${state.game_id}`;
  if (state.color !== lastColor) {
    cg.set({ orientation: state.color });
    lastColor = state.color;
  }
  if (state.fen && state.fen !== lastFen) {
    cg.set({ fen: state.fen });
    lastFen = state.fen;
    renderMoves(state.history);
  }
  cg.setAutoShapes(state.differing.map((key) => ({ orig: key, brush: 'yellow' })));
  setLight('bluetooth', state.lights.bluetooth);
  setLight('server', state.lights.server);
  setLight('sync', state.lights.sync);
  el('status').textContent = statusText(state);
  el('relay').hidden = !state.to_relay;
  el('relay').textContent = state.to_relay ? `Coup à reproduire sur l'échiquier : ${state.to_relay}` : '';
  const fixing = state.lights.sync === 'fixing' && state.differing.length > 0;
  el('differing').hidden = !fixing;
  el('differing').textContent = fixing ? `Cases à corriger : ${state.differing.join(', ')}` : '';
  el('fen').textContent = state.fen || '';
  if (!el('log').hidden) el('log').textContent = state.log.join('\n');
}

async function poll() {
  try {
    const response = await fetch('/state', { cache: 'no-store' });
    if (response.ok) render(await response.json());
  } catch (_err) {
    el('status').textContent = 'Le pont ne répond plus.';
  }
  setTimeout(poll, POLL_MS);
}

el('fen-btn').addEventListener('click', async () => {
  const fen = el('fen');
  fen.hidden = !fen.hidden;
  if (!fen.hidden && fen.textContent) {
    try { await navigator.clipboard.writeText(fen.textContent); } catch (_err) { /* shown anyway */ }
  }
});

el('log-btn').addEventListener('click', () => {
  const log = el('log');
  log.hidden = !log.hidden;
  el('log-btn').setAttribute('aria-expanded', String(!log.hidden));
});

poll();
