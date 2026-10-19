# Maquette conteneurisée sur le NAS (BT-008, #28)

Objectif : vérifier que palamede tourne sous Linux en conteneur, et que le
serveur MCP exposé en **Streamable HTTP** résiste à une veille du client et
à un redémarrage du conteneur, sans relancer la session (étage 2 de
outillages#320).

## Contenu

| Élément | Rôle |
|---|---|
| `Dockerfile` | image unique : Python 3.11-slim, Stockfish (`/usr/games/stockfish`), dépendances figées sur `poetry.lock`, utilisateur non root (uid 10001), volume `/data/games` |
| `docker-compose.maquette.yml` | `palamede-mcp` (`:8770/mcp/`, `/healthz`) et `palamede-viewer` (`:8765`), volume `palamede-games`, réseau `palamede-maquette` |
| `.github/workflows/docker-image.yml` | construction et smoke test sur chaque PR concernée ; publication sur GHCR au tag `vX.Y.Z` ou à la main |

## Transport Streamable HTTP sans état

`palamede-chessboard-server --transport http` (ou `MCP_CHESSBOARD_TRANSPORT=http`)
sert le MCP sur `/mcp/` avec `StreamableHTTPSessionManager(stateless=True)` du
SDK. Il n'y a aucun identifiant de session côté serveur : après une veille du
Mac ou un `docker restart palamede-mcp`, la requête suivante passe telle
quelle. L'état des parties vit sur disque (volume). Le stdio reste le mode
par défaut.

Pourquoi pas supergateway (prévu dans #28) : un runtime Node en plus dans
l'image, et un plantage connu sur la 2e session SSE (cf. commentaires du
sidecar mcp-talk). Le SDK Python fournit le transport nativement.

## Déploiement (stack Portainer)

```
PORTAINER_PROFILE=mnemosyne portainer-ctl deploy palamede-maquette \
  --compose docker-compose.maquette.yml --env <fichier contenant PALAMEDE_IMAGE=ghcr.io/stephanejouve/palamede-chessboard@sha256:…>
```

Client MCP de test (Claude Code) :
`claude mcp add --transport http palamede-nas http://192.168.1.79:8770/mcp/`

## Limites assumées

- **Aucune authentification** : ports publiés pour les seuls essais LAN.
  mTLS / sidecar et macvlan = étape suivante si la maquette est concluante.
- **Pas d'arbitre gardien** : le contrat « pidfile + PID vivant » ne traverse
  pas les espaces de PID de conteneurs distincts. La maquette passe
  `arbiter=<agent>` à `chess_create_game`.
- Données de test uniquement : ne jamais monter `/Users/Shared/games`.

## Critères d'acceptation (#28)

1. `up -d` : conteneurs sains.
2. Depuis un agent de test : `chess_create_game`, 4 coups, `chess_resign`.
3. Le viewer affiche la partie, `/api/elo` l'intègre.
4. Veille du Mac 10 min, puis `chess_list_games` OK sans relancer la session.
5. `docker restart palamede-mcp` pendant une session : l'appel suivant passe.
6. `down` puis `up` : parties conservées (volume).
7. Aucun secret dans l'image ni dans le compose.
