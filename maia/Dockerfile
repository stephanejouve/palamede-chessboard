# syntax=docker/dockerfile:1
# palamede-maia — the Maia bot (#93, PR 2), an image apart from palamède.
#
# lc0 (GPL-3.0) is built from its tagged source, and the Maia weights
# (GPL-3.0) are fetched from a pinned commit of CSSLab/maia-chess and checked
# against weights.sha256. Neither is committed to this repository (review
# Leader #93) ; their licence texts ship in /usr/share/doc/palamede-maia.

ARG LC0_VERSION=v0.32.1
ARG MAIA_COMMIT=749204cf5979ce7f8b0412e804a4ee7c83c49ff8

FROM debian:bookworm-slim AS lc0
ARG LC0_VERSION
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates git g++ meson ninja-build pkg-config zlib1g-dev libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${LC0_VERSION}" --recurse-submodules \
       https://github.com/LeelaChessZero/lc0.git /lc0
WORKDIR /lc0
# No GPU toolkit in this image : meson keeps the CPU backends (BLAS), no tests.
RUN meson setup build --buildtype=release -Dgtest=false -Dnative_arch=false \
    && ninja -C build lc0 \
    && install -D -m 755 build/lc0 /out/lc0 \
    && install -D -m 644 COPYING /out/LICENSE.lc0

FROM debian:bookworm-slim AS weights
ARG MAIA_COMMIT
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY weights.sha256 /weights/
WORKDIR /weights
RUN for f in $(awk '{print $2}' weights.sha256); do \
       curl -fsSL -o "$f" "https://raw.githubusercontent.com/CSSLab/maia-chess/${MAIA_COMMIT}/maia_weights/$f"; \
    done \
    && sha256sum -c weights.sha256

FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libopenblas0 zlib1g \
    && rm -rf /var/lib/apt/lists/*
COPY --from=lc0 /out/lc0 /usr/local/bin/lc0
COPY --from=weights /weights/*.pb.gz /opt/maia/weights/
COPY --from=lc0 /out/LICENSE.lc0 /usr/share/doc/palamede-maia/
COPY LICENSE /usr/share/doc/palamede-maia/LICENSE.maia
COPY README.md /usr/share/doc/palamede-maia/

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY palamede_maia ./palamede_maia
RUN pip install --no-cache-dir . && rm -rf /src

RUN useradd --create-home --uid 10002 maia
ENV MAIA_LC0=/usr/local/bin/lc0 \
    MAIA_WEIGHTS_DIR=/opt/maia/weights \
    MAIA_TOKENS_DIR=/run/secrets/maia \
    PYTHONUNBUFFERED=1
USER maia
WORKDIR /home/maia
CMD ["palamede-maia"]
