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
// « Enregistrer… » only in the app, which has a native dialog (#134).
let canSave = false;

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
// own, then one chip per piece followed by its squares, White then Black, each
// from the king to the pawn (#117). The bridge sends `put` already in that
// order ; the groups are spaced by the CSS gap, with no separator text.
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

// Out of the setup, the tray shows what each side has taken (#117) : the
// player's side first, the pieces strongest first, and « +N » beside the side
// ahead on material. A side that has taken nothing is left out, unless it is
// ahead : a promotion alone, say (review Admin #146).
function renderCaptures(taken, color) {
  const nodes = [];
  const order = color === 'black' ? ['black', 'white'] : ['white', 'black'];
  for (const side of order) {
    const pieces = taken ? taken[side === 'white' ? 'by_white' : 'by_black'] : [];
    const ahead = !taken ? 0 : side === 'white' ? taken.balance : -taken.balance;
    if (!pieces.length && ahead <= 0) continue;
    const chips = pieces.map((symbol) => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = symbol;
      return chip;
    });
    const span = group('taken', `${SIDE_NAME[side]} `, ...chips);
    if (ahead > 0) {
      const lead = document.createElement('span');
      lead.className = 'lead';
      lead.textContent = `+${ahead}`;
      span.append(lead);
    }
    nodes.push(span);
  }
  el('captures').replaceChildren(...nodes);
  return nodes.length > 0;
}

// m:ss, h:mm:ss beyond an hour, tenths under ten seconds (#135).
function clockText(seconds) {
  const s = Math.max(seconds, 0);
  if (s < 10) return s.toFixed(1);
  const whole = Math.floor(s);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const ss = String(whole % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

const SIDE_NAME = { white: 'Blancs', black: 'Noirs' };
const SIDE_PIECE = { white: '\u2654\ufe0e', black: '\u265a\ufe0e' };
// Under this many seconds, the clock turns red (#135).
const LOW_SECONDS = 30;

// Two faces level with the board, the opponent's above, the player's below
// (#135, Stéphane's choice) : the side to move lit up, the other dimmed.
function renderClocks(clock, color) {
  const sides = color === 'black' ? ['white', 'black'] : ['black', 'white'];
  for (const [id, side] of [['clock-top', sides[0]], ['clock-bottom', sides[1]]]) {
    const node = el(id);
    node.hidden = !clock;
    if (!clock) continue;
    node.classList.toggle('active', clock.active === side);
    node.classList.toggle('flag', clock.flag === side);
    node.classList.toggle('low', clock.flag !== side && clock[side] < LOW_SECONDS);
    node.querySelector('.who').textContent = clock.flag === side
      ? `${SIDE_PIECE[side]} ${SIDE_NAME[side]} : drapeau`
      : `${SIDE_PIECE[side]} ${SIDE_NAME[side]}`;
    node.querySelector('.time').textContent = clockText(clock[side]);
    node.title = clock.started ? '' : 'La pendule démarre au premier coup des Blancs.';
  }
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
  hidePgn();
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
  runMenus(state);
  if (openRequests !== null && state.open_requests !== openRequests) showPicker();
  openRequests = state.open_requests;
  // Plain text, selected in one click (#140) : the name is copied from the title.
  el('game-id-row').hidden = !state.game_id;
  if (el('game-id').textContent !== state.game_id) el('game-id').textContent = state.game_id || '';
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
  renderClocks(state.clock, state.color);
  el('status').textContent = statusText(state);
  el('relay').hidden = !state.to_relay;
  el('relay').textContent = state.relay_text || '';
  const fixing = state.lights.sync === 'fixing' && state.differing.length > 0;
  renderSetup(fixing ? state.setup : null);
  const anyTaken = renderCaptures(fixing ? null : state.captured, state.color);
  el('differing').hidden = !fixing;
  el('captures').hidden = !anyTaken;
  el('tray').hidden = !fixing && !anyTaken;
  el('tray').setAttribute('aria-label', fixing ? 'Mise en place' : 'Pièces prises');
  if (el('fen').value !== (state.fen || '')) el('fen').value = state.fen || '';
  canSave = Boolean(state.can_save);
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
function flipBoard() {
  cg.toggleOrientation();
  el('flip-btn').setAttribute('aria-pressed', String(cg.state.orientation !== lastColor));
}

el('flip-btn').addEventListener('click', flipBoard);

// A read-only field, not text : the window disables text selection (pywebview),
// and the clipboard API may be refused there (review of Stéphane, 28/10). The
// FEN, the game's name and its PGN (#134) are copied the same way.
async function copyField(button, field, copiedLabel) {
  field.focus();
  field.select();
  let copied = false;
  try {
    await navigator.clipboard.writeText(field.value);
    copied = true;
  } catch (_err) {
    copied = document.execCommand('copy');
  }
  if (copied) {
    button.dataset.label ??= button.textContent;
    button.textContent = copiedLabel;
    setTimeout(() => { button.textContent = button.dataset.label; }, 1500);
  }
}

function toggleCopy(button, field, copiedLabel) {
  field.hidden = !field.hidden;
  if (!field.hidden && field.value) copyField(button, field, copiedLabel);
}

el('fen-btn').addEventListener('click', () => toggleCopy(el('fen-btn'), el('fen'), 'FEN copiée'));

function exportMessage(message) {
  el('export-msg').hidden = !message;
  el('export-msg').textContent = message || '';
}

function hidePgn() {
  el('pgn').hidden = true;
  el('pgn').value = '';
  el('pgn-save').hidden = true;
  exportMessage('');
}

// Read again at each opening : the PGN grows with the moves (#134). The bridge
// reads the viewer's public /api/raw, never with the token.
async function readPgn() {
  exportMessage('Lecture du PGN…');
  try {
    const response = await fetch('/pgn', { cache: 'no-store' });
    const answer = await response.json().catch(() => ({ error: `réponse illisible (HTTP ${response.status})` }));
    if (!response.ok) {
      exportMessage(answer.error);
      return false;
    }
    el('pgn').value = answer.pgn;
  } catch (_err) {
    exportMessage('Le pont ne répond plus.');
    return false;
  }
  exportMessage('');
  el('pgn').hidden = false;
  el('pgn-save').hidden = !canSave;
  return true;
}

el('pgn-btn').addEventListener('click', async () => {
  if (!el('pgn').hidden) {
    hidePgn();
    return;
  }
  if (await readPgn()) copyField(el('pgn-btn'), el('pgn'), 'PGN copié');
});

// The app's native dialog : the bridge writes the file, the page never sees the path.
async function savePgn() {
  // One dialog at a time : the bridge refuses a second one too (review Junior #137).
  el('pgn-save').disabled = true;
  try {
    const response = await fetch('/save-pgn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Palamede-Nonce': NONCE },
      body: JSON.stringify({ game_id: lastGame }),
    });
    const answer = await response.json().catch(() => ({ error: `réponse illisible (HTTP ${response.status})` }));
    if (!response.ok) {
      exportMessage(answer.error);
      return;
    }
    exportMessage(answer.saved ? `PGN enregistré : ${answer.saved}` : '');
  } catch (_err) {
    exportMessage('Le pont ne répond plus.');
  } finally {
    el('pgn-save').disabled = false;
  }
}

el('pgn-save').addEventListener('click', savePgn);

// « Édition ▸ Copier … » when the bridge has no system clipboard (#140) : the
// copy runs outside any click, WebKit may refuse it ; the text is then shown
// selected, for ⌘C.
async function menuCopy(field, copiedText) {
  if (!field.value) {
    exportMessage('Rien à copier : la partie n’est pas encore lue.');
    return;
  }
  field.hidden = false;
  field.focus();
  field.select();
  let copied = false;
  try {
    await navigator.clipboard.writeText(field.value);
    copied = true;
  } catch (_err) {
    copied = document.execCommand('copy');
  }
  exportMessage(copied ? copiedText : 'Copie refusée par la fenêtre : le texte est sélectionné, ⌘C pour le copier.');
}

function selectGameName() {
  const range = document.createRange();
  range.selectNodeContents(el('game-id'));
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  let copied = false;
  try {
    copied = document.execCommand('copy');
  } catch (_err) {
    copied = false;
  }
  exportMessage(copied ? 'Nom copié.' : 'Le nom est sélectionné dans le titre : ⌘C pour le copier.');
}

// The window's menus (#90, #140) : a counter per item in /state, the action
// run once when it changes, as a click on the button would.
const MENU_ACTIONS = {
  'save-pgn': savePgn,
  'copy-name': selectGameName,
  'copy-fen': () => menuCopy(el('fen'), 'FEN copiée.'),
  'copy-pgn': async () => { if (await readPgn()) menuCopy(el('pgn'), 'PGN copié.'); },
  flip: flipBoard,
};
let lastMenu = null;
let lastNotice = null;

function runMenus(state) {
  if (state.menu) {
    if (lastMenu !== null) {
      for (const [action, count] of Object.entries(state.menu)) {
        if (count !== lastMenu[action] && MENU_ACTIONS[action]) MENU_ACTIONS[action]();
      }
    }
    lastMenu = state.menu;
  }
  // A copy done by the bridge itself (system clipboard) : just say so.
  if (state.notice) {
    if (lastNotice !== null && state.notice.n !== lastNotice) exportMessage(state.notice.text);
    lastNotice = state.notice.n;
  }
}

el('log-btn').addEventListener('click', () => {
  const log = el('log');
  log.hidden = !log.hidden;
  el('log-btn').setAttribute('aria-expanded', String(!log.hidden));
});

poll();
