// Local page of the app (#78) : polls the bridge's /state. Without a board,
// the player's moves are made here with the mouse (#92) and sent to the
// bridge, which checks them and posts them with the token.
import { Chessground } from '/vendor/chessground.min.js';

const POLL_MS = 500;
const NONCE = document.querySelector('meta[name="palamede-nonce"]').content;
const LIGHT_CLASS = {
  on: 'on', searching: 'warn', connecting: 'warn', reconnecting: 'warn', fixing: 'warn',
  checking: 'warn', unchecked: 'warn', refused: 'bad',
  // The board's link : blue over Bluetooth, green over USB, red when it keeps failing.
  ble: 'blue', usb: 'on', failing: 'bad', closed: 'waiting',
};

// Not viewOnly : chessground only binds the mouse at creation. Nobody may
// move until renderMouse() gives the player's colour (never with a board).
const cg = Chessground(document.getElementById('board'), {
  fen: 'start',
  movable: { free: false, color: undefined },
  premovable: { enabled: false },
  draggable: { showGhost: true },
  coordinates: true,
  animation: { enabled: true, duration: 200 },
  drawable: { enabled: false },
});

const el = (id) => document.getElementById(id);
let lastFen = null;
let lastColor = null;
let lastDests = '';
let sending = false;
let lastGame = null;
// The menu « Fichier ▸ Ouvrir » bumps a counter in /state (#90).
let openRequests = null;

// Squares to fix : a red LED in the corner, as on the eBoard (review of
// Stéphane, 28/10), rather than drawn circles. Set only when they change.
let lastLeds = null;
function renderLeds(squares) {
  const key = squares.join();
  if (key === lastLeds) return;
  lastLeds = key;
  cg.set({ highlight: { custom: new Map(squares.map((square) => [square, 'led'])) } });
}

function setLight(name, value) {
  const node = el(`light-${name}`);
  node.hidden = value === 'none';
  node.className = `light ${LIGHT_CLASS[value] || 'waiting'}`;
}

function showMoveError(message) {
  el('move-error').hidden = !message;
  el('move-error').textContent = message || '';
}

async function sendMove(orig, dest, promotions) {
  const uci = orig + dest + (promotions.includes(orig + dest) ? el('promotion').value : '');
  sending = true;
  cg.set({ movable: { color: undefined, dests: new Map() } });
  try {
    const response = await fetch('/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Palamede-Nonce': NONCE },
      body: JSON.stringify({ uci }),
    });
    // A non-JSON answer is not « no answer » (review Junior #97).
    const answer = await response.json().catch(() => ({ error: `réponse illisible (HTTP ${response.status})` }));
    showMoveError(response.ok ? '' : answer.error);
    if (!response.ok) cg.set({ fen: lastFen });
  } catch (_err) {
    showMoveError('Le pont ne répond plus.');
    cg.set({ fen: lastFen });
  }
  sending = false;
  lastDests = '';
}

// Only the player's colour, only on their turn : the bridge sends the dests.
function renderMouse(state) {
  el('promotion-row').hidden = !state.mouse;
  if (!state.mouse || sending) return;
  const key = JSON.stringify([state.fen, state.dests]);
  if (key === lastDests) return;
  lastDests = key;
  const dests = new Map(Object.entries(state.dests));
  cg.set({
    turnColor: state.fen && state.fen.split(' ')[1] === 'w' ? 'white' : 'black',
    movable: {
      free: false,
      color: dests.size ? state.color : undefined,
      dests,
      showDests: true,
      events: { after: (orig, dest) => sendMove(orig, dest, state.promotions) },
    },
  });
}

function group(className, ...nodes) {
  const span = document.createElement('span');
  span.className = `group ${className}`;
  span.append(...nodes);
  return span;
}

// In the tray under the board, in the order of the gestures (review of
// Stéphane, Admin, Junior, 28/10) : the squares to clear as a group of their
// own, then one chip per piece with its squares, by type from the king to the
// pawn (#117) : « Retirer b7 c1 · ♕ c4 · ♝ a5 · ♟ e6 c3 ». The bridge sends
// `put` already in that order.
function renderSetup(setup) {
  const nodes = [];
  if (setup && setup.remove.length) {
    nodes.push(group('remove', `Retirer ${setup.remove.map((r) => r.square).join(' ')}`));
  }
  let last = null;
  for (const step of (setup ? setup.put : [])) {
    if (!last || last.colour !== step.colour || last.symbol !== step.symbol) {
      const chip = document.createElement('span');
      chip.className = `chip ${step.colour}`;
      chip.textContent = step.symbol;
      nodes.push(group('put', chip));
    }
    nodes[nodes.length - 1].append(` ${step.square}`);
    last = step;
  }
  el('differing').replaceChildren(...nodes);
}

function statusText(state) {
  if (!state.fen) return 'En attente de la partie…';
  if (state.finished || (state.mouse && !state.live)) return 'Partie terminée.';
  if (!state.live) return 'Partie terminée : remettez la position finale sur l\'échiquier.';
  const turn = state.fen.split(' ')[1] === 'w' ? 'white' : 'black';
  return turn === state.color ? 'À vous de jouer.' : 'Au tour de l\'adversaire.';
}

// Numbered from the game's own move numbers : a game set up with Black to move
// starts with « 29. … c3 », not « 1. c3 » (008d, review of Stéphane, 28/10).
function renderMoves(history, firstPly) {
  const list = el('moves');
  const items = [];
  let ply = Math.max(firstPly || 0, 0);
  let i = 0;
  while (i < history.length) {
    const li = document.createElement('li');
    li.value = Math.floor(ply / 2) + 1;
    const take = ply % 2 === 1 ? 1 : 2;
    const moves = history.slice(i, i + take);
    // Three aligned columns : number, White, Black (review of Stéphane, 28/10).
    // « … » holds White's place when the game starts with Black.
    for (const san of [`${li.value}.`, ...(take === 1 ? ['…', ...moves] : moves)]) {
      const cell = document.createElement('span');
      cell.textContent = san;
      li.append(cell);
    }
    items.push(li);
    i += take;
    ply += take;
  }
  list.replaceChildren(...items);
  list.scrollTop = list.scrollHeight;
}

// Another game opened (#90) : nothing of the previous one may stay on screen.
function forgetGame() {
  lastFen = null;
  lastColor = null;
  lastDests = '';
  lastLeds = null;
  cg.set({ fen: '8/8/8/8/8/8/8/8', lastMove: undefined });
  el('moves').replaceChildren();
  showMoveError('');
}

function pickerMessage(message) {
  el('picker-msg').hidden = !message;
  el('picker-msg').textContent = message || '';
}

async function openGame(gameId) {
  pickerMessage('Ouverture…');
  try {
    const response = await fetch('/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Palamede-Nonce': NONCE },
      body: JSON.stringify({ game_id: gameId }),
    });
    const answer = await response.json().catch(() => ({ error: `réponse illisible (HTTP ${response.status})` }));
    if (!response.ok) {
      pickerMessage(answer.error);
      return;
    }
    el('picker').hidden = true;
  } catch (_err) {
    pickerMessage('Le pont ne répond plus.');
  }
}

// One button per game in progress : « 008d — stephane contre relais ».
function gameItem(game, current) {
  const li = document.createElement('li');
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = `${game.game_id} — ${game.white} contre ${game.black}`;
  if (game.game_id === current) {
    button.disabled = true;
    button.textContent += ' (ouverte)';
  }
  button.addEventListener('click', () => openGame(game.game_id));
  li.append(button);
  return li;
}

async function showPicker() {
  el('picker').hidden = false;
  el('picker-list').replaceChildren();
  pickerMessage('Recherche de vos parties…');
  try {
    const response = await fetch('/games', { cache: 'no-store' });
    const answer = await response.json().catch(() => ({ error: `réponse illisible (HTTP ${response.status})` }));
    if (!response.ok) {
      pickerMessage(answer.error);
      return;
    }
    pickerMessage(answer.games.length ? '' : `Aucune partie en cours pour ${answer.account}.`);
    el('picker-list').replaceChildren(...answer.games.map((game) => gameItem(game, answer.current)));
  } catch (_err) {
    pickerMessage('Le pont ne répond plus.');
  }
}

function render(state) {
  if (state.game_id !== lastGame) {
    if (lastGame !== null) forgetGame();
    lastGame = state.game_id;
  }
  if (openRequests !== null && state.open_requests !== openRequests) showPicker();
  openRequests = state.open_requests;
  el('game').textContent = `Échiquier : ${state.game_id}`;
  if (state.color !== lastColor) {
    cg.set({ orientation: state.color });
    lastColor = state.color;
    el('flip-btn').setAttribute('aria-pressed', 'false');
  }
  if (state.fen && state.fen !== lastFen) {
    cg.set({ fen: state.fen });
    lastFen = state.fen;
    renderMoves(state.history, state.first_ply);
  }
  renderMouse(state);
  renderLeds(state.differing);
  setLight('bluetooth', state.lights.bluetooth);
  el('light-bluetooth').textContent = state.transport === 'usb' ? 'USB' : 'Bluetooth';
  setLight('server', state.lights.server);
  setLight('sync', state.lights.sync);
  setLight('token', state.lights.token);
  // The account name only : the page never gets the token (#78, step A).
  el('light-token').textContent = state.account ? `Jeton : ${state.account}` : 'Jeton';
  el('token-msg').hidden = !state.token_message;
  // Orange while unchecked, red when refused : the colour of the light.
  el('token-msg').classList.toggle('warn', state.lights.token !== 'refused');
  el('token-msg').textContent = state.token_message || '';
  el('status').textContent = statusText(state);
  el('relay').hidden = !state.to_relay;
  el('relay').textContent = state.relay_text || '';
  const fixing = state.lights.sync === 'fixing' && state.differing.length > 0;
  el('tray').hidden = !fixing;
  renderSetup(fixing ? state.setup : null);
  if (el('fen').value !== (state.fen || '')) el('fen').value = state.fen || '';
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

el('open-btn').addEventListener('click', showPicker);
el('picker-close').addEventListener('click', () => { el('picker').hidden = true; });

// Display only (#91) : the bridge, the moves allowed and /state are unchanged.
el('flip-btn').addEventListener('click', () => {
  cg.toggleOrientation();
  el('flip-btn').setAttribute('aria-pressed', String(cg.state.orientation !== lastColor));
});

// A read-only field, not text : the window disables text selection (pywebview),
// and the clipboard API may be refused there (review of Stéphane, 28/10).
el('fen-btn').addEventListener('click', async () => {
  const fen = el('fen');
  fen.hidden = !fen.hidden;
  if (fen.hidden || !fen.value) return;
  fen.focus();
  fen.select();
  let copied = false;
  try {
    await navigator.clipboard.writeText(fen.value);
    copied = true;
  } catch (_err) {
    copied = document.execCommand('copy');
  }
  if (copied) {
    el('fen-btn').textContent = 'FEN copiée';
    setTimeout(() => { el('fen-btn').textContent = 'FEN'; }, 1500);
  }
});

el('log-btn').addEventListener('click', () => {
  const log = el('log');
  log.hidden = !log.hidden;
  el('log-btn').setAttribute('aria-expanded', String(!log.hidden));
});

poll();
