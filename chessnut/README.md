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

On the board :

- set the pieces up : the squares still wrong light up ; a short sweep of
  the LEDs says the board matches the game, from then on your moves count ;
- play your move : it is sent once the pieces stay still ;
- the opponent's move lights up its squares : play it for them ;
- to resign, lift your king off the board for 5 seconds (the LEDs blink
  meanwhile ; put it back to cancel).
