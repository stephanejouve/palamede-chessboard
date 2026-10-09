# palamede-chessboard

Serveur MCP de parties d'échecs multi-agents.

Extrait du monorepo [`outillages`](https://github.com/stephanejouve/outillages) (série de PR 2026-10) vers un repo métier dédié pour :

- isoler la surface chess (12 modules, 5446 LoC + 10 fichiers de tests) qui n'a plus de raison de cohabiter avec les outils cross-projet
- auditer et refactorer les god scripts (`mcp_chessboard_server.py` 1382 LoC en tête) en modules thématiques
- rendre la base conforme à la politique PII (ce repo est **PUBLIC**)
- faire tourner la CI contre un vrai moteur `stockfish` (apt-get) plutôt qu'une batterie de mocks

## Phases du chantier

| Phase | Scope | État |
|-------|-------|------|
| 0 | Skeleton (pyproject, structure, CI 3 jobs, placeholder test) | **en cours** — cette PR |
| 1 | Extraction code 1:1 depuis outillages + nettoyage PII + ajustement imports | à venir |
| 2 | Refactor god scripts en sous-modules thématiques | à venir |
| N | Suppression des modules chess dans outillages (une fois palamede opérationnel) | à venir |

## Développement

Prérequis : Python 3.11+, [Poetry](https://python-poetry.org/), `stockfish` (optionnel localement, requis en CI).

```bash
poetry install
poetry run pre-commit install
poetry run pytest
```

## Doctrine review

- Double APPROVE requis avant merge sur `main` (comme outillages).
- Reviews stale dismissées automatiquement sur chaque push.
- Les 3 jobs CI (`lint`, `typecheck`, `pytest`) doivent être verts.

## Release

Auto-bump via [release-please](https://github.com/googleapis/release-please-action) sur merge vers `main`. Les Conventional Commits (`feat:`, `fix:`, `BREAKING CHANGE:`) accumulés dans les PR mergées sont reflétés dans une **PR de release** ouverte en continu — merger cette PR produit le tag `vX.Y.Z` + une release GitHub avec changelog.

Pattern pilote pour généralisation cross-repos (candidate à propager vers `outillages`, `telephonIA`, `amalthee`, `mcp-proxy`).

## Licence

MIT (voir [`LICENSE`](LICENSE)).
