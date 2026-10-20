# chessground 9.2.1 (embarqué)

Plateau d'échecs de lichess, <https://github.com/lichess-org/chessground>.
**Licence : GPL-3.0-or-later**, texte complet dans `LICENSE` (ce
répertoire). Le reste de palamede est sous MIT.

Embarqué pour #59 : la page du viewer détient le jeton du joueur, aucun
script ne doit venir d'une origine tierce. Le viewer sert ces fichiers sous
`/vendor/chessground@9.2.1/`.

## Provenance

Fichiers copiés à l'identique depuis l'archive npm
`https://registry.npmjs.org/chessground/-/chessground-9.2.1.tgz`, dont
l'empreinte correspond à l'`integrity` du registre :
`sha512-KIdxm0zD69e62XRwdnFB0XUNYSzn5HM3pMazRCwpfruRUowFI8y+1OAK5YSyBff29myeV1/4o5Q8T191+Irbog==`.

| fichier                    | chemin dans l'archive          | sha256 |
|----------------------------|--------------------------------|--------|
| `chessground.min.js`       | `dist/chessground.min.js`      | `915457e1a6b84abd9778d3ea5317a9fccaf59f4caa78196efbd92f586c0f0e9b` |
| `chessground.base.css`     | `assets/chessground.base.css`  | `77cbfd65f9b3f64f310a0b8eab0e34ef9f364172e4f678fc05c114b385a341a6` |
| `chessground.brown.css`    | `assets/chessground.brown.css` | `80de613fe928a947e88b983835286f21c723738d9319b1f8868442bb56fb1577` |
| `chessground.cburnett.css` | `assets/chessground.cburnett.css` | `f9216192b5383a83e62a81de1cf58520f7c251f6f24930f5710a3c797f78ced4` |
| `LICENSE`                  | `LICENSE`                      | `8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903` |

`tests/viewer/test_vendor_chessground.py` vérifie ces empreintes. Pour
monter de version : remplacer les fichiers depuis la nouvelle archive,
vérifier son `integrity`, puis mettre à jour `CHESSGROUND_VERSION`
(`viewer/models.py`), ce tableau et le test.
