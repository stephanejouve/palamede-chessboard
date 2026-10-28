# palamede-chessnut

Play a game of the palamède viewer on a Chessnut Air+ electronic board,
over Bluetooth LE or its USB cable, without the vendor app.

```
uv run --with 'palamede-chessnut @ git+https://github.com/stephanejouve/palamede-chessboard#subdirectory=chessnut' \
  palamede-chessnut --viewer https://<viewer> --game <game_id> --color white --token-file <file>
```

The player token (the one behind the viewer's 🔑 button) is read from a
file readable by its owner only, or from `PALAMEDE_PLAYER_TOKEN`. Where to
keep the file :

- macOS, Linux : anywhere, once `chmod 600` ; any other mode is refused ;
- Windows : under your user profile (`%USERPROFILE%`), whose default access
  rights keep it to you ; a file elsewhere, even through a link, is refused.
  Prefer `%APPDATA%\palamede` to Documents or the Desktop, often synced by
  OneDrive. Rights changed by hand on the file, or a hard link to it, are not
  detected.

## Settings file (#90)

`--viewer`, `--token-file` and `--transport` may come from a per-user file,
so that only the game remains on the command line :

```toml
# ~/.config/palamede/config.toml  (macOS, Linux ; $XDG_CONFIG_HOME if set)
# %APPDATA%\palamede\config.toml  (Windows)
viewer = "https://<viewer>"
token_file = "~/.config/palamede/token"
transport = "auto"
```

```
palamede-chessnut --game <game_id> --color white
```

`--config <file>` or `PALAMEDE_CONFIG` name another file. Options given on
the command line win over it. The file names the token *file*, never the
token : a `token` key is refused. The viewer URL and the token file are
checked as when given as options (https, `chmod 600`). An unknown key is
logged and ignored.

On the board :

- set the pieces up : the squares still wrong light up ; a short sweep of
  the LEDs says the board matches the game, from then on your moves count ;
- play your move : it is sent once the pieces stay still ;
- the opponent's move lights up its squares : play it for them ;
- to resign, lift your king off the board for 5 seconds (the LEDs blink
  meanwhile ; put it back to cancel).

## Over the USB cable or Bluetooth (#82, #110)

By default (`--transport auto`), the bridge takes the USB cable when it
links the board to this computer, Bluetooth otherwise, and switches during
the game : plug the cable in and Bluetooth is closed, USB opened ; unplug
it and the bridge looks for the board over Bluetooth again. The board
feeds one link at a time (a Bluetooth central takes its pieces, the cable
then only charges), hence Bluetooth is closed before USB is opened. A
cable to a charger does not count. The light shows the link in use, « USB »
or « Bluetooth » ; a switch only resynchronises, no move is lost.
`--transport usb` or `--transport ble` force one of them.

If the board is plugged in but USB cannot be used (rule, extra or
permission below missing), the bridge says so once and stays on
Bluetooth until the cable is unplugged and plugged again.

- Linux : nothing to install, but your session needs access to the
  board's `/dev/hidrawN`. Once, as root, write the rule
  `SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2d80", ATTRS{idProduct}=="8202", MODE="0660", TAG+="uaccess"`
  into `/etc/udev/rules.d/70-chessnut.rules`, run
  `udevadm control --reload`, then replug the board.
- macOS, Windows : install the `usb` extra
  (`palamede-chessnut[usb]`, or `palamede-chessnut[app,usb]` for the
  window), which brings hidapi. On macOS the board also declares itself a
  keyboard : the system may ask to allow « Input Monitoring » for the
  terminal that starts the bridge (System Settings, Privacy & Security).
  It does not type anything, checked on 26/10.

## The « Échiquier » window (#78)

The same bridge, with a window that shows the game, the moves, three
lights (Bluetooth, server, synchronisation) and the squares still to fix :

```
uv run --with 'palamede-chessnut[app] @ git+https://github.com/stephanejouve/palamede-chessboard#subdirectory=chessnut' \
  palamede-chessnut-app --viewer https://<viewer> --game <game_id> --color white --token-file <file>
```

Same options as `palamede-chessnut`. The `app` extra brings pywebview :
WebKit on macOS, Edge WebView2 on Windows, Qt on Linux. The window reads
a page served by the bridge on `127.0.0.1` only ; the player token stays
in the bridge and never reaches the page. Closing the window stops the
bridge, LEDs off. With a board, the window only shows the game : moves are
played on the board, which is the reference.

Without a board, add `--no-board` : the bridge looks for no Chessnut, and
the player's moves are made with the mouse in the window (click-click or
drag, legal squares shown, promotion piece chosen beside the board). The
page asks the bridge for the move ; the bridge checks it and posts it with
the token, which still never reaches the page. That request must come from
the page itself : its `Origin`, and a nonce drawn at each launch and sent
in the `X-Palamede-Nonce` header (#92).

At launch, the bridge asks the viewer whose token it holds
(`GET /api/games/<game>/seat`) : the « Jeton » light turns green with the
account name, or red with the reason (token refused, account not in the
game, wrong `--color`), and mouse moves wait for a green light. An older
viewer without that route leaves the light orange : the first move tells.
The log gets the token's length and a short SHA-256 fingerprint, never the
token ; the page gets neither.

The page embeds chessground 9.2.1 (lichess), under GPL-3.0-or-later : its
licence, provenance and TypeScript source ship with it, in
`palamede_chessnut/vendor/chessground/`. The rest of the bridge is MIT.

On macOS, the first launch asks to allow Bluetooth for the terminal (or the
app) that starts the bridge : without it, the Bluetooth light stays orange.
The window needs a graphical session ; when it cannot open, the bridge
stops, LEDs off.
