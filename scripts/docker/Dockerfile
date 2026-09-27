ARG QGIS_IMAGE=qgis/qgis:3.44.11
FROM ${QGIS_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fontconfig=2.15.0-2.3ubuntu1 fonts-noto-core=20201225-2 \
    && rm -rf /var/lib/apt/lists/*

# Unmodified open font files, licence and pinned upstream provenance.
COPY fonts/barlow/ /usr/local/share/fonts/qfit/barlow/
# Runtime rendering/tests do not need root. Only dependency installation in
# the test runner explicitly selects root; the QGIS profile belongs to qfit.
RUN fc-cache -f \
    && useradd --create-home --shell /bin/bash qfit \
    && sed -i 's|/root/|${HOME}/|g' /usr/bin/qgis_setup.sh
ENV HOME=/home/qfit
USER qfit
