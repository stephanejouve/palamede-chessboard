# Changelog

## [0.14.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.13.0...v0.14.0) (2026-10-30)


### Features

* **chessnut:** the actions in the window's menus, the name selectable, ⇅ ([#141](https://github.com/stephanejouve/palamede-chessboard/issues/141)) ([c14a7ff](https://github.com/stephanejouve/palamede-chessboard/commit/c14a7ffd27c4a5c056d6b6fcf9a3d31e4f50e611))
* **chessnut:** the clocks as compact faces level with the board ([#135](https://github.com/stephanejouve/palamede-chessboard/issues/135)) ([#144](https://github.com/stephanejouve/palamede-chessboard/issues/144)) ([fdfc404](https://github.com/stephanejouve/palamede-chessboard/commit/fdfc4048a6f45e170f597756b190019f5e3cbcfe))


### Bug Fixes

* **chessnut:** the lit clock face amber in the light theme, the lit red a token ([#145](https://github.com/stephanejouve/palamede-chessboard/issues/145)) ([2f96399](https://github.com/stephanejouve/palamede-chessboard/commit/2f96399136434fd8590fc95dacc9f58003e66f12))

## [0.13.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.12.0...v0.13.0) (2026-10-29)


### Features

* **chessnut:** copy the game's name, and its PGN to copy or save ([#137](https://github.com/stephanejouve/palamede-chessboard/issues/137)) ([5c00cc6](https://github.com/stephanejouve/palamede-chessboard/commit/5c00cc6bce43108d732b4a1f6d8fb6f9a5c01a51))
* **chessnut:** the two clocks in the window, read from the viewer ([#139](https://github.com/stephanejouve/palamede-chessboard/issues/139)) ([6a258b7](https://github.com/stephanejouve/palamede-chessboard/commit/6a258b7668181fcfd5434f598191eb37482b0093))
* **clock:** the clock runs on every move path, Tablier games start on the first move ([#136](https://github.com/stephanejouve/palamede-chessboard/issues/136)) ([171e1c3](https://github.com/stephanejouve/palamede-chessboard/commit/171e1c345c425d27870c049341b118bf51ac80ff))

## [0.12.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.11.0...v0.12.0) (2026-10-29)


### Features

* **chessnut:** the setup in a tray under the board, by colour then type of piece ([#125](https://github.com/stephanejouve/palamede-chessboard/issues/125)) ([b899ca3](https://github.com/stephanejouve/palamede-chessboard/commit/b899ca3230a8d8373669f522067f20812baa92df))
* **maia:** the Maia bot, lc0 and the Maia 1 weights in an image of its own ([#131](https://github.com/stephanejouve/palamede-chessboard/issues/131)) ([eadc348](https://github.com/stephanejouve/palamede-chessboard/commit/eadc3484a50f760bbf9898cd4e9ee3f35bff694c))

## [0.11.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.10.0...v0.11.0) (2026-10-29)


### Features

* **chessnut:** launch Tablier without --game nor --color, through GET /api/me ([#90](https://github.com/stephanejouve/palamede-chessboard/issues/90)) ([b41191a](https://github.com/stephanejouve/palamede-chessboard/commit/b41191a420477c7848ba2cce65c26fc757e614c4))
* **viewer:** POST /api/games, an unrated game opened by a player's token ([#126](https://github.com/stephanejouve/palamede-chessboard/issues/126)) ([6810e62](https://github.com/stephanejouve/palamede-chessboard/commit/6810e62366c25f0935a0bae5fb74fe35063b5a84))

## [0.10.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.9.0...v0.10.0) (2026-10-28)


### Features

* **chessnut:** « Fichier ▸ Ouvrir », another game of the player in the same window ([#121](https://github.com/stephanejouve/palamede-chessboard/issues/121)) ([6e5fd0a](https://github.com/stephanejouve/palamede-chessboard/commit/6e5fd0a990efdec513f518a9cbd0a7f635a4933a))
* **chessnut:** a per-user settings file for the viewer, the token file and the transport ([#124](https://github.com/stephanejouve/palamede-chessboard/issues/124)) ([72e16e6](https://github.com/stephanejouve/palamede-chessboard/commit/72e16e6b47bf3cc54490fc6be54445fb3eadd129))
* **chessnut:** red LED dots for squares to fix, FEN copy, text selection ([#118](https://github.com/stephanejouve/palamede-chessboard/issues/118)) ([00af6f4](https://github.com/stephanejouve/palamede-chessboard/commit/00af6f4891a4d8913323f38393930120690a6b49))
* **chessnut:** the board setup in piece symbols on colour chips ([#115](https://github.com/stephanejouve/palamede-chessboard/issues/115)) ([e89f276](https://github.com/stephanejouve/palamede-chessboard/commit/e89f276f002fefba636cdf555e2fba5ca6b3d430))
* **chessnut:** the move list in aligned columns, number, White, Black ([#120](https://github.com/stephanejouve/palamede-chessboard/issues/120)) ([db28b05](https://github.com/stephanejouve/palamede-chessboard/commit/db28b054bd8011fc1b73974100b1a274f83a4b2e))


### Bug Fixes

* **chessnut:** « Retirer » before « Poser », a black piece in the setup test ([#116](https://github.com/stephanejouve/palamede-chessboard/issues/116)) ([6b24938](https://github.com/stephanejouve/palamede-chessboard/commit/6b249381f04f0c2f8d35fb17f55bd195c7435cb3))
* **chessnut:** reopen a stopped game, and close the window after any opening ([#123](https://github.com/stephanejouve/palamede-chessboard/issues/123)) ([d082432](https://github.com/stephanejouve/palamede-chessboard/commit/d08243286b357f63b86f3e327cc39baebe0942c0))
* **chessnut:** the board light is grey once a finished game's bridge stops ([#113](https://github.com/stephanejouve/palamede-chessboard/issues/113)) ([e47d881](https://github.com/stephanejouve/palamede-chessboard/commit/e47d88176fb7754b8b1bc9b8ac27e5eb7762148a))
* **chessnut:** the move list numbered from the game, not from « 1. » ([#119](https://github.com/stephanejouve/palamede-chessboard/issues/119)) ([229992d](https://github.com/stephanejouve/palamede-chessboard/commit/229992d6278e67a2e9e8c376b22b9cff190acc58))


### Tests

* **chessnut:** opening a game keeps the board and its transport ([#122](https://github.com/stephanejouve/palamede-chessboard/issues/122)) ([f508088](https://github.com/stephanejouve/palamede-chessboard/commit/f50808890a45d60fdeb494c8e1f244f276477906))

## [0.9.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.8.0...v0.9.0) (2026-10-28)


### Features

* **chessnut:** --transport auto, the cable when plugged in, Bluetooth otherwise ([#111](https://github.com/stephanejouve/palamede-chessboard/issues/111)) ([0d1ebd5](https://github.com/stephanejouve/palamede-chessboard/commit/0d1ebd52195376aad2420dacb7d9d6a10fc4b85d))
* **chessnut:** button to flip the board in the app ([#101](https://github.com/stephanejouve/palamede-chessboard/issues/101)) ([e636968](https://github.com/stephanejouve/palamede-chessboard/commit/e6369688f1e3780668f1201efe4f60cb33c3808c))
* **chessnut:** play with the mouse in the app, without a board ([#97](https://github.com/stephanejouve/palamede-chessboard/issues/97)) ([09c5346](https://github.com/stephanejouve/palamede-chessboard/commit/09c5346cea6da736d4208b49a5a2d4c5ff07e35a))
* **chessnut:** reach the board over its USB cable, --transport usb ([#108](https://github.com/stephanejouve/palamede-chessboard/issues/108)) ([799cb08](https://github.com/stephanejouve/palamede-chessboard/commit/799cb0828a5f84c217aba1e10ba8fe7294910261))
* **chessnut:** read-only app window for the bridge ([#87](https://github.com/stephanejouve/palamede-chessboard/issues/87)) ([48bff6b](https://github.com/stephanejouve/palamede-chessboard/commit/48bff6be68d9868cec016fa965a5e893c84a7446))
* **chessnut:** the board light says the link and its trouble ([#112](https://github.com/stephanejouve/palamede-chessboard/issues/112)) ([6da9deb](https://github.com/stephanejouve/palamede-chessboard/commit/6da9debc655f8986ccb450db67774bb9eb5c0615))
* **chessnut:** token light in the app, checked at launch ([#106](https://github.com/stephanejouve/palamede-chessboard/issues/106)) ([7b46a1d](https://github.com/stephanejouve/palamede-chessboard/commit/7b46a1d888067c6a27fd9044b2775c63c6fc9450))
* **viewer:** GET /api/games/{id}/seat, the token's player and side ([#102](https://github.com/stephanejouve/palamede-chessboard/issues/102)) ([3f407a6](https://github.com/stephanejouve/palamede-chessboard/commit/3f407a6e3547ce5c7e72e3e86eeb633397d0b49a))


### Bug Fixes

* **chessnut:** plainer page messages, after the USB smoke ([#109](https://github.com/stephanejouve/palamede-chessboard/issues/109)) ([43fbfc7](https://github.com/stephanejouve/palamede-chessboard/commit/43fbfc7452617bd688e264bf4df30964af02cae1))
* **chessnut:** token file check on Windows ([#88](https://github.com/stephanejouve/palamede-chessboard/issues/88)) ([fcd4f97](https://github.com/stephanejouve/palamede-chessboard/commit/fcd4f97ee10df80f6e71b346e1288710ec933823))
* **viewer:** a missing game's 404 names the game, not the PGN path ([#105](https://github.com/stephanejouve/palamede-chessboard/issues/105)) ([a0c43a3](https://github.com/stephanejouve/palamede-chessboard/commit/a0c43a395e2ee8ce5e5ddb8238a058fb333aa7a8))

## [0.8.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.7.0...v0.8.0) (2026-10-26)


### Features

* **chessnut:** FEN at the ready sweep, resignation retried once the stream is back ([#77](https://github.com/stephanejouve/palamede-chessboard/issues/77)) ([ed3ef27](https://github.com/stephanejouve/palamede-chessboard/commit/ed3ef273f7d702756697550782eb38d134e38002))
* **chessnut:** log the move relayed on the board, SAN next to UCI ([#79](https://github.com/stephanejouve/palamede-chessboard/issues/79)) ([0831b99](https://github.com/stephanejouve/palamede-chessboard/commit/0831b99d633490f07fe838aeb812f5ec4b28bf02))
* **chessnut:** own package, ready sweep, resignation by lifting the king, dark board at the end ([#75](https://github.com/stephanejouve/palamede-chessboard/issues/75)) ([dee705a](https://github.com/stephanejouve/palamede-chessboard/commit/dee705a3d7e30eb58f1b085b04f2adbfb28a55f6))
* **viewer:** flip the board, Black at the bottom for the black player ([#86](https://github.com/stephanejouve/palamede-chessboard/issues/86)) ([230defe](https://github.com/stephanejouve/palamede-chessboard/commit/230defe152122c76abc9277d0c703b2396c20c79))

## [0.7.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.6.3...v0.7.0) (2026-10-24)


### Features

* **chessnut:** Bluetooth LE bridge between a Chessnut Air+ and a viewer game ([#71](https://github.com/stephanejouve/palamede-chessboard/issues/71)) ([643890c](https://github.com/stephanejouve/palamede-chessboard/commit/643890c15f97706da4b189d79e70292fb7ecc510))
* **viewer:** resignation with the player's token, POST /api/games/&lt;id&gt;/resign ([#73](https://github.com/stephanejouve/palamede-chessboard/issues/73)) ([60f6960](https://github.com/stephanejouve/palamede-chessboard/commit/60f69602850eb02b640aa6246342f73e3fa61855))

## [0.6.3](https://github.com/stephanejouve/palamede-chessboard/compare/v0.6.2...v0.6.3) (2026-10-21)


### Bug Fixes

* **viewer:** un coup refusé ne bloque plus les coups suivants ([#70](https://github.com/stephanejouve/palamede-chessboard/issues/70)) ([0bad5fc](https://github.com/stephanejouve/palamede-chessboard/commit/0bad5fcfe1f5fc59988ff489ebcb03f1c5cb3b85))
* **viewer:** vider les bornes du plateau au début de chaque geste ([#68](https://github.com/stephanejouve/palamede-chessboard/issues/68)) ([a617156](https://github.com/stephanejouve/palamede-chessboard/commit/a617156409ce913701444cbc6e5ed903b23322b8))

## [0.6.2](https://github.com/stephanejouve/palamede-chessboard/compare/v0.6.1...v0.6.2) (2026-10-20)


### Bug Fixes

* **viewer:** Content-Security-Policy sur la page ([#63](https://github.com/stephanejouve/palamede-chessboard/issues/63)) ([4a0f893](https://github.com/stephanejouve/palamede-chessboard/commit/4a0f893e5f6597f4011eb17b72f242cb6083c788))
* **viewer:** le drag&drop du plateau fonctionne enfin ([#64](https://github.com/stephanejouve/palamede-chessboard/issues/64)) ([e13ceb8](https://github.com/stephanejouve/palamede-chessboard/commit/e13ceb818eb0610204e6f2c68d1fcf4926b53225))

## [0.6.1](https://github.com/stephanejouve/palamede-chessboard/compare/v0.6.0...v0.6.1) (2026-10-20)


### Bug Fixes

* **viewer:** embarquer chessground au lieu du CDN ([#60](https://github.com/stephanejouve/palamede-chessboard/issues/60)) ([a3d52a6](https://github.com/stephanejouve/palamede-chessboard/commit/a3d52a618e6f917b3f1c917a92e18ff74dd58346))

## [0.6.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.5.0...v0.6.0) (2026-10-20)


### Features

* **players:** mode observation de l'authentification ([#55](https://github.com/stephanejouve/palamede-chessboard/issues/55)) ([7d045b4](https://github.com/stephanejouve/palamede-chessboard/commit/7d045b4b64310a6893d395cbb4f6529ad0f850ac))
* **players:** registre natif des joueurs et jetons d'API ([#54](https://github.com/stephanejouve/palamede-chessboard/issues/54)) ([613a2e3](https://github.com/stephanejouve/palamede-chessboard/commit/613a2e333553031512210eb01d01a61ca6eea902))
* **viewer:** favicon cavalier sur case bicolore ([#56](https://github.com/stephanejouve/palamede-chessboard/issues/56)) ([a3f9f2f](https://github.com/stephanejouve/palamede-chessboard/commit/a3f9f2fff4d093d33cbe889eb48cc1140feb3a2e))
* **viewer:** un coup joué depuis le viewer exige le jeton du joueur au trait ([#58](https://github.com/stephanejouve/palamede-chessboard/issues/58)) ([4122e5b](https://github.com/stephanejouve/palamede-chessboard/commit/4122e5b0d33d4cc7776745dac7d1ea2d0d677d33))

## [0.5.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.4.0...v0.5.0) (2026-10-20)


### Features

* **viewer:** sélecteur de parties paginé, filtrable, sans rechargement global ([#52](https://github.com/stephanejouve/palamede-chessboard/issues/52)) ([21929a3](https://github.com/stephanejouve/palamede-chessboard/commit/21929a369a42feeea74548e6ad7fee86b5c35305)), closes [#46](https://github.com/stephanejouve/palamede-chessboard/issues/46)


### Bug Fixes

* corrections de [#46](https://github.com/stephanejouve/palamede-chessboard/issues/46) (pendule, game_id, garde moteur, fsync) ([#47](https://github.com/stephanejouve/palamede-chessboard/issues/47)) ([21ff75b](https://github.com/stephanejouve/palamede-chessboard/commit/21ff75b974a818229ba237852e3cb7ada56dbc52))
* **lessons:** suivis de [#46](https://github.com/stephanejouve/palamede-chessboard/issues/46) sur le carnet de leçons ([#49](https://github.com/stephanejouve/palamede-chessboard/issues/49)) ([34874a3](https://github.com/stephanejouve/palamede-chessboard/commit/34874a39c0f86ce78852f565401f4efda8f43f11))
* **lessons:** un PGN illisible fait sauter l'analyse, sans repli sur le .md ([#53](https://github.com/stephanejouve/palamede-chessboard/issues/53)) ([47e2bcc](https://github.com/stephanejouve/palamede-chessboard/commit/47e2bcc6441addb4068e2ad442ea63096ae1fdc9)), closes [#46](https://github.com/stephanejouve/palamede-chessboard/issues/46)


### Refactor

* **mcp:** découper _mcp_tool_schemas par famille d'outils, seuils vérifiés en CI ([#50](https://github.com/stephanejouve/palamede-chessboard/issues/50)) ([5cdcb44](https://github.com/stephanejouve/palamede-chessboard/commit/5cdcb4444706eac8c38afda080535fd3bd5a3fa8)), closes [#46](https://github.com/stephanejouve/palamede-chessboard/issues/46)
* **viewer:** découper le template en includes (861 → 78 lignes) ([#51](https://github.com/stephanejouve/palamede-chessboard/issues/51)) ([b97eef7](https://github.com/stephanejouve/palamede-chessboard/commit/b97eef736ad9323a50f9d63c4973e081dea50880)), closes [#46](https://github.com/stephanejouve/palamede-chessboard/issues/46)

## [0.4.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.3.0...v0.4.0) (2026-10-20)


### Features

* préférence max_concurrent par joueur pour le matchmaking ([#40](https://github.com/stephanejouve/palamede-chessboard/issues/40)) ([57762bb](https://github.com/stephanejouve/palamede-chessboard/commit/57762bb278921264c5dca01985e158c3af0cbf3c))


### Bug Fixes

* **prefs:** normaliser les clés joueur à la lecture des préférences ([#42](https://github.com/stephanejouve/palamede-chessboard/issues/42)) ([b933737](https://github.com/stephanejouve/palamede-chessboard/commit/b933737f9a76112f21db93cce21faa205c489c33))
* **viewer:** partir de la position de départ de la partie, pas de la position standard ([#44](https://github.com/stephanejouve/palamede-chessboard/issues/44)) ([abf21a5](https://github.com/stephanejouve/palamede-chessboard/commit/abf21a54f44a45a1f0d168e5811d06dcbe8b3257)), closes [#43](https://github.com/stephanejouve/palamede-chessboard/issues/43)

## [0.3.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.2.0...v0.3.0) (2026-10-20)


### Features

* **mcp:** paramètre rated dans chess_create_game, parties non classées hors matchmaking ([#33](https://github.com/stephanejouve/palamede-chessboard/issues/33)) ([a9649ba](https://github.com/stephanejouve/palamede-chessboard/commit/a9649baee12e42682600ce11a450871917338a33))
* **mcp:** paramètre rated dans chess_create_game, parties non classées hors matchmaking ([#35](https://github.com/stephanejouve/palamede-chessboard/issues/35)) ([8999021](https://github.com/stephanejouve/palamede-chessboard/commit/899902171b99e77f5d7407adf2be0b27e59d932c))


### Bug Fixes

* **state:** persister starting_fen (SetUp/FEN) et numéroter les coups depuis la position de départ ([#36](https://github.com/stephanejouve/palamede-chessboard/issues/36)) ([61cea6c](https://github.com/stephanejouve/palamede-chessboard/commit/61cea6c469b6a9553b2cdde2dfcfd95a21c34874))
* **state:** persister starting_fen et numéroter les coups depuis la position de départ ([#37](https://github.com/stephanejouve/palamede-chessboard/issues/37)) ([04ffc97](https://github.com/stephanejouve/palamede-chessboard/commit/04ffc97091849a9348956dce2d0d94d8f0cba927))

## [0.2.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.1.0...v0.2.0) (2026-10-19)


### Features

* **docker:** maquette conteneurisée, MCP Streamable HTTP sans état et viewer (BT-008) ([#31](https://github.com/stephanejouve/palamede-chessboard/issues/31)) ([8607a73](https://github.com/stephanejouve/palamede-chessboard/commit/8607a7312f9972e7d5a5ed1b37dd5d2631b6d4a3))

## [0.1.0](https://github.com/stephanejouve/palamede-chessboard/compare/v0.0.1...v0.1.0) (2026-10-11)


### Features

* **lessons:** carnet cumulé des sections Leçons (V3, closes [#11](https://github.com/stephanejouve/palamede-chessboard/issues/11)) ([200b955](https://github.com/stephanejouve/palamede-chessboard/commit/200b95516df1d57e61adee173d87c37945f40f74))
* **lessons:** carnet cumulé des sections Leçons (V3, closes [#11](https://github.com/stephanejouve/palamede-chessboard/issues/11)) ([7e19dc2](https://github.com/stephanejouve/palamede-chessboard/commit/7e19dc259207d3af9b34750e6920922cbe92ea96))
* **phase-0:** skeleton projet + CI 3 jobs ([cb88afc](https://github.com/stephanejouve/palamede-chessboard/commit/cb88afc3c21b05c79320b9852dee72150582dfef))
* **phase-0:** skeleton projet + CI 3 jobs ([e593ecc](https://github.com/stephanejouve/palamede-chessboard/commit/e593eccf17ed11eb3de8072a75905f16a3537ddc))
* **release:** auto-bump via release-please (pattern pilote cross-repos) ([b826bdd](https://github.com/stephanejouve/palamede-chessboard/commit/b826bdd36c6d5ded782e3d59005c94a70f6385ad))


### Bug Fixes

* **#14:** ruff format test_pgn_scan.py ([90ea44f](https://github.com/stephanejouve/palamede-chessboard/commit/90ea44f9ba08da24ebc68d976bc415db1fc2b0cb))
* **#14:** unify GAMES_DIR through palamede_chessboard.paths ([7061abf](https://github.com/stephanejouve/palamede-chessboard/commit/7061abfa3abf31fa6d8bfafdd2860b6f821c2d1b))
* **#14:** unify GAMES_DIR through palamede_chessboard.paths (prérequis BT-008) ([0adf2d1](https://github.com/stephanejouve/palamede-chessboard/commit/0adf2d137efdba6caf438750a668c6c7cfe5cc15))
* **engine-guard:** fermer le contournement game_id fantôme ([#19](https://github.com/stephanejouve/palamede-chessboard/issues/19)) ([0c1ce83](https://github.com/stephanejouve/palamede-chessboard/commit/0c1ce839fc8c3b2057fe9fe26be07d812f2d0d0a))
* **engine-guard:** fermer le contournement game_id fantôme ([#19](https://github.com/stephanejouve/palamede-chessboard/issues/19)) ([74a42d3](https://github.com/stephanejouve/palamede-chessboard/commit/74a42d328c410fd2d5048cd1eea643e8f8011c20))
* **engine-guard:** fermer le contournement game_id fantôme ([#19](https://github.com/stephanejouve/palamede-chessboard/issues/19)) ([c397614](https://github.com/stephanejouve/palamede-chessboard/commit/c3976149cc4fd0556588c52db6d7c3c639264e4b))
* **engine-guard:** fermer le contournement game_id fantôme ([#19](https://github.com/stephanejouve/palamede-chessboard/issues/19)) ([02543c3](https://github.com/stephanejouve/palamede-chessboard/commit/02543c326c15725e86b12fcb137a27696a4412a9))
* **phase-0:** nits CD review [#1](https://github.com/stephanejouve/palamede-chessboard/issues/1) ([e199c8a](https://github.com/stephanejouve/palamede-chessboard/commit/e199c8a301450bd84f06a2e0925fee43b4bec54a))


### Refactor

* **bt-001:** extraction chessboard_state + chessboard_clock ([f1575b2](https://github.com/stephanejouve/palamede-chessboard/commit/f1575b274ce5e1cd7f7426fe726f9f40a76e8dd9))
* **bt-001:** extraction chessboard_state + chessboard_clock ([85c6ac8](https://github.com/stephanejouve/palamede-chessboard/commit/85c6ac8170856e8059d6a75baf276010d5a47449))
* **bt-001:** extraction chessboard_state + chessboard_clock ([c4eaadf](https://github.com/stephanejouve/palamede-chessboard/commit/c4eaadf8414631f86c55f8faff3cdcc6b52e0b8d))
* **bt-001:** extraction chessboard_state + chessboard_clock ([94508db](https://github.com/stephanejouve/palamede-chessboard/commit/94508dbd7a181a6b0dc85f387317640ada0d87f2))
* **bt-001:** extraction chessboard_state + chessboard_clock ([9a6eb14](https://github.com/stephanejouve/palamede-chessboard/commit/9a6eb14d453c52f1ce5afaed9157398bc25d54b9))
* **bt-002:** extraction chessboard_engine (M2 Stockfish) ([aa0d66b](https://github.com/stephanejouve/palamede-chessboard/commit/aa0d66b34a21dd50161eb0a7e5926900f644212b))
* **bt-002:** extraction chessboard_engine (M2 Stockfish) ([cc9f7f7](https://github.com/stephanejouve/palamede-chessboard/commit/cc9f7f7cc0f3132c68e255054e97079368a54551))
* **bt-002:** extraction chessboard_engine (M2 Stockfish) ([1ea8c6d](https://github.com/stephanejouve/palamede-chessboard/commit/1ea8c6df2b028267ae76e803443fab6bb2c8b954))
* **bt-003:** extraction chess_arbiter + chess_engine_guard ([63b9013](https://github.com/stephanejouve/palamede-chessboard/commit/63b9013c696ff6de472bb9bb49522d327ae14f5a))
* **bt-003:** extraction chess_arbiter + chess_engine_guard ([3a329b1](https://github.com/stephanejouve/palamede-chessboard/commit/3a329b18140edb9e5cf79eb99d44547e3412adc3))
* **bt-003:** extraction chess_arbiter + chess_engine_guard ([8161305](https://github.com/stephanejouve/palamede-chessboard/commit/8161305c6a53de71f431510ef5b020214c27b9fa))
* **bt-003:** extraction chess_arbiter + chess_engine_guard ([4f5b8cd](https://github.com/stephanejouve/palamede-chessboard/commit/4f5b8cd1c6d66e2f87355a76848b2fc93ebf02fa))
* **bt-003:** extraction chess_arbiter + chess_engine_guard ([e049331](https://github.com/stephanejouve/palamede-chessboard/commit/e0493316d68b773abb803e681bd48fd54b79eabf))
* **bt-005:** extraction chess_arbiter_daemon (M5) + install LaunchAgent ([988f67a](https://github.com/stephanejouve/palamede-chessboard/commit/988f67acf6343842a5d15c58b40f4e18426c08d5))
* **bt-005:** extraction chess_arbiter_daemon (M5) + install LaunchAgent ([d87b76d](https://github.com/stephanejouve/palamede-chessboard/commit/d87b76d14a02d7fa5dc275175f0c8a05cc2459f5))
* **bt-005:** extraction chess_arbiter_daemon (M5) + install LaunchAgent ([ff1a70c](https://github.com/stephanejouve/palamede-chessboard/commit/ff1a70c15add534d00123b087d97ccecd57d2708))
* **bt-005:** extraction chess_arbiter_daemon (M5) + install LaunchAgent ([879fc46](https://github.com/stephanejouve/palamede-chessboard/commit/879fc4602100dd74d3f80e74fcc7a1dfb6cebf0d))
* **bt-005:** extraction chess_arbiter_daemon (M5) + install LaunchAgent ([33225e7](https://github.com/stephanejouve/palamede-chessboard/commit/33225e72f32d29ba9ce3f2e62290f58f70d54532))
* **bt-005:** extraction chess_arbiter_daemon (M5) + install LaunchAgent ([6f48bca](https://github.com/stephanejouve/palamede-chessboard/commit/6f48bcaa92b97e1b6121e0c62cb0fe43fe8efffa))
* **bt-005:** process_message split ≤80l + raccord MCP server entry point ([10d743f](https://github.com/stephanejouve/palamede-chessboard/commit/10d743fe6cdf196af9c9b94b7d498dae7e6a1ea4))
* **bt-005:** process_message split ≤80l + raccord MCP server entry point ([6671d4c](https://github.com/stephanejouve/palamede-chessboard/commit/6671d4c0b2dc00bbac49f635bec8d61e424d59a3))
* **bt-005:** process_message split ≤80l + raccord MCP server entry point ([2ff4026](https://github.com/stephanejouve/palamede-chessboard/commit/2ff4026a652cacd0ba9a5cac62feeb2a1f860f05))
* **bt-006:** extraction chess_elo (V1) ([05c419d](https://github.com/stephanejouve/palamede-chessboard/commit/05c419d968029b454940e8887b10d07af633164c))
* **bt-006:** extraction chess_elo (V1) ([47e7ce3](https://github.com/stephanejouve/palamede-chessboard/commit/47e7ce301900c6b32e282e454d17d5d1bbc4ac44))
* **bt-006:** extraction chess_elo (V1) ([dd32220](https://github.com/stephanejouve/palamede-chessboard/commit/dd322209c1b0257f9b5fbe72f3fd43b10bd3ffcc))
* **bt-006:** extraction chess_matchmaking (V2) ([3b8a45d](https://github.com/stephanejouve/palamede-chessboard/commit/3b8a45db60053412c081068164e460daf234e89d))
* **bt-006:** extraction chess_matchmaking (V2) ([540ffd2](https://github.com/stephanejouve/palamede-chessboard/commit/540ffd234ecac28dca85a7e18f550b03cbc0b89b))
* **bt-006:** extraction chess_matchmaking (V2) ([b19ff75](https://github.com/stephanejouve/palamede-chessboard/commit/b19ff75d3063bed05de0a8dcc01e73c9298bcf3a))
* **bt-007:** viewer split APIRouter par domaine + CLIs + template ([784a199](https://github.com/stephanejouve/palamede-chessboard/commit/784a19996b2f1aab5b455b7699f18facdbfe7815))
* **bt-007:** viewer split APIRouter par domaine + CLIs + template ([2e170a8](https://github.com/stephanejouve/palamede-chessboard/commit/2e170a89c1079410076c754ef3851363f6dc14fe))
* **bt-007:** viewer split APIRouter par domaine + CLIs + template ([b9b5188](https://github.com/stephanejouve/palamede-chessboard/commit/b9b5188b6827d313c9f47cf5ea7ee52e151a73c3))
* **bt-007:** viewer split APIRouter par domaine + CLIs + template ([555d5e5](https://github.com/stephanejouve/palamede-chessboard/commit/555d5e5baa985846fefe0f453af81488cc9bdb11))


### Tests

* **mcp:** split test_mcp_chessboard_server.py 1471l → 9 modules ≤303l ([5afeba1](https://github.com/stephanejouve/palamede-chessboard/commit/5afeba17a902f7a936c81bec9574145f764d4342))
* **mcp:** split test_mcp_chessboard_server.py 1471l → 9 modules ≤303l ([7abeb72](https://github.com/stephanejouve/palamede-chessboard/commit/7abeb72acd05adbe5920b4b15aff2641bc382d82))
* **viewer:** split test_chessboard_viewer.py 785l → 6 modules ≤193l ([fcaedd4](https://github.com/stephanejouve/palamede-chessboard/commit/fcaedd4fb0eac016b8254699f35e828a768c691e))
* **viewer:** split test_chessboard_viewer.py 785l → 6 modules ≤193l ([94746b4](https://github.com/stephanejouve/palamede-chessboard/commit/94746b43ee132ca95a73d606ac6b972eab5c8665))
