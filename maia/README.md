# palamede-maia

The Maia bot of palamède (#93) : human-like moves for the `bot-maia-<elo>`
accounts, so that a human can play Maia from Tablier.

- **Engine** : [lc0](https://github.com/LeelaChessZero/lc0) `v0.32.1`, built
  from its tagged source in the image, CPU only (BLAS backend).
- **Weights** : the nine Maia 1 networks, `maia-1100` to `maia-1900`, from
  [CSSLab/maia-chess](https://github.com/CSSLab/maia-chess) at commit
  `749204cf5979ce7f8b0412e804a4ee7c83c49ff8`, checked against
  `weights.sha256` at build time. Played at **one node per move**, as Maia is
  meant to be (the `maia1` to `maia9` bots of Lichess).

## Rules

Maia plays against humans, never as a source of moves for the agents
(owner and Leader, #93, 29/10) :

- only for the `bot-maia-<elo>` accounts whose token it holds, when that
  account is to move ; the server checks the token as for any player ;
- only **unrated** games ;
- only against an opponent listed in `--humans` (`MAIA_HUMANS`) : never an
  agent, never another bot, nobody when the list is empty ;
- no MCP tool exposes Maia : an agent cannot consult it.

## Running

```
palamede-maia --viewer http://127.0.0.1:8766 \
  --tokens-dir /run/secrets/maia --humans stephane,cilou
```

The tokens directory holds `bot-maia-<elo>.token` files, mode `600` ; the
levels played are the files present. A token is never logged. The bot reads
the games in progress every two seconds and plays through
`POST /api/games/<id>/move`.

## Licences

The bot's code, lc0 and the Maia weights are under the **GPL-3.0**
(`LICENSE` here, `COPYING` of lc0, `LICENSE` of maia-chess ; copies in
`/usr/share/doc/palamede-maia` in the image). Nothing of them is linked into
palamède, which stays MIT : the bot is a separate process that talks to lc0
over UCI and to palamède over HTTP. Neither lc0 nor the weights are
committed to this repository.
