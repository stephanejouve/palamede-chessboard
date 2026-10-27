# palamede-chessnut

Play a game of the palamède viewer on a Chessnut Air+ electronic board,
over Bluetooth LE, without the vendor app.

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

On the board :

- set the pieces up : the squares still wrong light up ; a short sweep of
  the LEDs says the board matches the game, from then on your moves count ;
- play your move : it is sent once the pieces stay still ;
- the opponent's move lights up its squares : play it for them ;
- to resign, lift your king off the board for 5 seconds (the LEDs blink
  meanwhile ; put it back to cancel).

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
bridge, LEDs off. The window only shows the game : moves are still played
on the board.
