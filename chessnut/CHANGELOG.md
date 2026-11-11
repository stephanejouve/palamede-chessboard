# Changelog

## [0.6.0](https://github.com/stephanejouve/palamede-chessboard/compare/palamede-chessnut-v0.5.2...palamede-chessnut-v0.6.0) (2026-11-11)


### Features

* **chessnut:** each series of beeps in the log (S2 step 4, 08/11) ([b86dd01](https://github.com/stephanejouve/palamede-chessboard/commit/b86dd01f835f19292bcaa85b3e82fd3ff1a97258))
* **chessnut:** the board's sound code, the bishops open an exercise, the knights the lesson ([#186](https://github.com/stephanejouve/palamede-chessboard/issues/186), step 2) ([8e47606](https://github.com/stephanejouve/palamede-chessboard/commit/8e4760657cc38e2d24faba913d870fab4fa0622b))
* **chessnut:** the board's sound code, the bishops open an exercise, the knights the lesson ([#186](https://github.com/stephanejouve/palamede-chessboard/issues/186), step 2) ([8e250da](https://github.com/stephanejouve/palamede-chessboard/commit/8e250da95bf122a4579e5fe9052cfd2204b7426c))
* **exercise:** answer attacked squares on the board ([3b3ffa2](https://github.com/stephanejouve/palamede-chessboard/commit/3b3ffa2eb1fe5dcc774bd59f502a32e46fc1e75d))
* **exercise:** répondre aux « Cases attaquées » depuis l'échiquier ([4699043](https://github.com/stephanejouve/palamede-chessboard/commit/46990437f3b60ea48de9a591246fcf784ab30b99))


### Bug Fixes

* **chessnut:** hidapi's manager reached from one thread only, a missing hidapi said once ([#296](https://github.com/stephanejouve/palamede-chessboard/issues/296), [#295](https://github.com/stephanejouve/palamede-chessboard/issues/295)) ([ef62a0b](https://github.com/stephanejouve/palamede-chessboard/commit/ef62a0bd92ac9e952fa4316b61420b67e9c29b26))
* **chessnut:** hidapi's manager reached from one thread only, a missing hidapi said once ([#296](https://github.com/stephanejouve/palamede-chessboard/issues/296), [#295](https://github.com/stephanejouve/palamede-chessboard/issues/295)) ([a95a4b1](https://github.com/stephanejouve/palamede-chessboard/commit/a95a4b1efd32a971e614f512ed07cf653f22601f))
* **exercise:** a move is no answer to an attacked-squares exercise ([2f0eeda](https://github.com/stephanejouve/palamede-chessboard/commit/2f0eeda7c4009573098104e6137b998224e14800))
* **exercise:** king lifts validate, designated squares are green ([3b68936](https://github.com/stephanejouve/palamede-chessboard/commit/3b689365fd3a0308966127f5f647b5fb278b40f2))
* **exercise:** la consigne d'abord, l'erreur d'origine en contexte ([f505858](https://github.com/stephanejouve/palamede-chessboard/commit/f505858de6ffb486865db249f7bb9848012f81fe))
* **exercise:** the order first, the mistake of the game as context ([2050e7a](https://github.com/stephanejouve/palamede-chessboard/commit/2050e7a0c57afda446a0a5742becfda5889ec08c))
* **tablier:** a minimum window size keeps the board readable ([0a3952a](https://github.com/stephanejouve/palamede-chessboard/commit/0a3952ae9510175f3bcf7918bb6335033ae7fd4f))
* **tablier:** descendre les lettres de colonnes sous le socle des pièces ([37f23b5](https://github.com/stephanejouve/palamede-chessboard/commit/37f23b518034cd4aa066d06fbeb10e426a852437))
* **tablier:** keep file letters in their square on a small board ([de87751](https://github.com/stephanejouve/palamede-chessboard/commit/de87751a9f5ca1e3148db5914e16878fcc1917a1))
* **tablier:** l'échiquier ne grandit plus sous le seuil de 760 px ([a8a6ac4](https://github.com/stephanejouve/palamede-chessboard/commit/a8a6ac4ab8a4a34adbebb99d8b3b0f164a9faee2))
* **tablier:** lower the file letters under the pieces' base ([c5402ab](https://github.com/stephanejouve/palamede-chessboard/commit/c5402abdb7876866b6d10e601a5cd12a3a9b000c))
* **tablier:** the board no longer grows below the 760 px threshold ([ba16ec3](https://github.com/stephanejouve/palamede-chessboard/commit/ba16ec3527ae2dc3ed43e4ea607b1fdbafaad6e5))


### Refactor

* **chessnut:** split lobby.Lobby.tick into named steps (C901 16 → 5) ([#318](https://github.com/stephanejouve/palamede-chessboard/issues/318)) ([5b8cb51](https://github.com/stephanejouve/palamede-chessboard/commit/5b8cb51790910ca5f7b75833c5b8a33e3e4908ce))
* **chessnut:** the bridge's five network points behind a GameServer ([#285](https://github.com/stephanejouve/palamede-chessboard/issues/285)) ([0e629b3](https://github.com/stephanejouve/palamede-chessboard/commit/0e629b3c52da6da9eb53bb503422a512e3cf1276))


### Tests

* **chessnut:** a game's refused move is silent (review Junior [#293](https://github.com/stephanejouve/palamede-chessboard/issues/293)) ([44051ee](https://github.com/stephanejouve/palamede-chessboard/commit/44051eeda39dc9295ae538aedf954ca42fcf3ec3))
* **chessnut:** no hand-over after the animation of a clock choice ([#321](https://github.com/stephanejouve/palamede-chessboard/issues/321)) ([3dd1ccd](https://github.com/stephanejouve/palamede-chessboard/commit/3dd1ccdd376bb4b43ec9d16277fb0554a555e381))
* **chessnut:** two sessions at once still reach hidapi from one thread (review Junior [#297](https://github.com/stephanejouve/palamede-chessboard/issues/297)) ([7b30e76](https://github.com/stephanejouve/palamede-chessboard/commit/7b30e7610b40f3d3b45fa398a8df77b88836ce36))

## [0.5.2](https://github.com/stephanejouve/palamede-chessboard/compare/palamede-chessnut-v0.5.1...palamede-chessnut-v0.5.2) (2026-11-07)


### Bug Fixes

* **chessnut:** in the lobby, the window draws the board as read, not the finished game ([#263](https://github.com/stephanejouve/palamede-chessboard/issues/263)) ([4accdd5](https://github.com/stephanejouve/palamede-chessboard/commit/4accdd50bf75573b14daa31427ced575cae359c2))

## [0.5.1](https://github.com/stephanejouve/palamede-chessboard/compare/palamede-chessnut-v0.5.0...palamede-chessnut-v0.5.1) (2026-11-07)


### Bug Fixes

* **chessnut:** a game over never set up on the board stops without guidance ([#259](https://github.com/stephanejouve/palamede-chessboard/issues/259)) ([681d80f](https://github.com/stephanejouve/palamede-chessboard/commit/681d80f4f35ba64ad39009229fb714b97c6c8d0a))

## [0.5.0](https://github.com/stephanejouve/palamede-chessboard/compare/palamede-chessnut-v0.4.0...palamede-chessnut-v0.5.0) (2026-11-06)


### Features

* **tablier:** a refused move blinks its squares and says why, on the board and with the mouse ([#226](https://github.com/stephanejouve/palamede-chessboard/issues/226)) ([2fce35e](https://github.com/stephanejouve/palamede-chessboard/commit/2fce35e1423f1c04a9e0b5eb818a917d64773e66))
* **tablier:** Aide ▸ À propos gives the build, in the journal and a native dialog ([#228](https://github.com/stephanejouve/palamede-chessboard/issues/228)) ([cd04ee0](https://github.com/stephanejouve/palamede-chessboard/commit/cd04ee0095ceea28fbf2227a09e3cd0d9bcda404))
* **tablier:** an exercise says its motif, family and how to answer it ([#238](https://github.com/stephanejouve/palamede-chessboard/issues/238)) ([e48aafb](https://github.com/stephanejouve/palamede-chessboard/commit/e48aafb66706b2d1e488604f541b392a29de3741))

## [0.4.0](https://github.com/stephanejouve/palamede-chessboard/compare/palamede-chessnut-v0.3.0...palamede-chessnut-v0.4.0) (2026-11-05)


### Features

* **chessnut:** a new game from any placement, the two corners lifted together ([#214](https://github.com/stephanejouve/palamede-chessboard/issues/214)) ([7e0a72f](https://github.com/stephanejouve/palamede-chessboard/commit/7e0a72f4315508366f63df4b9f245413845291cf))


### Tests

* **chessnut:** the lobby's link gap measured on two real ticks ([#220](https://github.com/stephanejouve/palamede-chessboard/issues/220)) ([822e7dd](https://github.com/stephanejouve/palamede-chessboard/commit/822e7dd654e5ac261eadba83b6dde6f5429509b4))

## [0.3.0](https://github.com/stephanejouve/palamede-chessboard/compare/palamede-chessnut-v0.2.0...palamede-chessnut-v0.3.0) (2026-11-04)


### Features

* **chessnut:** the white knights choose the clock, said under the board ([#191](https://github.com/stephanejouve/palamede-chessboard/issues/191)) ([4cc7523](https://github.com/stephanejouve/palamede-chessboard/commit/4cc7523ddfac0f87365b481a1aeb02c344595136))
* **chessnut:** the games the lobby opens default to 15+10 ([#189](https://github.com/stephanejouve/palamede-chessboard/issues/189)) ([e6eb7b0](https://github.com/stephanejouve/palamede-chessboard/commit/e6eb7b0093cb11791b0a7e05345c985e0fd5ad9d))

## [0.2.0](https://github.com/stephanejouve/palamede-chessboard/compare/palamede-chessnut-v0.1.0...palamede-chessnut-v0.2.0) (2026-11-03)


### Bug Fixes

* **chessnut:** a coordinate takes the colour opposite to its square ([#184](https://github.com/stephanejouve/palamede-chessboard/issues/184)) ([03eeed5](https://github.com/stephanejouve/palamede-chessboard/commit/03eeed5ae357d8f6e941a8171863d81a40ba35c9))
* **chessnut:** a king held up while setting up again is not a resignation ([#179](https://github.com/stephanejouve/palamede-chessboard/issues/179)) ([#181](https://github.com/stephanejouve/palamede-chessboard/issues/181)) ([c3798c0](https://github.com/stephanejouve/palamede-chessboard/commit/c3798c0e6fe28f6b923a247022a963b8b38b63ce))
