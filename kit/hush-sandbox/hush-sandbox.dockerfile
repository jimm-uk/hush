# hush-sandbox: the one generic Pi sandbox kit, used for any folder.
# Pi + Office/PDF tooling + the hush-anon anonymisation workflow, talking to
# the MLX server on the host. No Node.js, no npm: Pi is the upstream
# self-contained binary, checked against a pinned SHA-256. Sandboxes made from
# it are throwaway (one per hush session); the image is cached by content.
#
# Supply-chain pins (bump deliberately, together):
#   - base image  : by digest
#   - Pi          : release tarball + SHA-256 per arch (from the GitHub release
#                   API digest and the release's SHA256SUMS)
#   - Python deps : files/requirements.lock, installed with --require-hashes
FROM docker/sandbox-templates:shell@sha256:7a248c96459694d181d9d51ffc9e530c0b9a4cb3d1887cc070425336b1fb5115

USER root
ENV DEBIAN_FRONTEND=noninteractive

# --- Remove Node.js / npm from the base image --------------------------------
# Nothing in the sandbox needs them, and anything the agent could run with
# them is attack surface.
RUN apt-get purge -y 'nodejs' 'npm' 'libnode*' 'node-*' \
    && apt-get autoremove -y --purge \
    && rm -rf /usr/lib/node_modules /usr/local/lib/node_modules /usr/local/share/npm-global \
              /root/.npm /home/agent/.npm \
    && ! command -v node && ! command -v npm

# --- System tools ------------------------------------------------------------------
# LibreOffice (headless conversions, accept-changes, .doc/.xls/.ppt -> OOXML),
# pandoc, PDF/OCR tooling, XML and archive utilities, metric-compatible fonts
# so conversions don't reflow (Carlito ~ Calibri, Caladea ~ Cambria).
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-draw \
      default-jre-headless \
      fonts-liberation fonts-dejavu fonts-crosextra-carlito fonts-crosextra-caladea fonts-noto-core \
      pandoc \
      poppler-utils qpdf ghostscript mupdf-tools \
      tesseract-ocr tesseract-ocr-eng \
      imagemagick libimage-exiftool-perl \
      antiword catdoc unrtf \
      libxml2-utils xmlstarlet \
      jq ripgrep fd-find tree file less vim-tiny nano \
      zip unzip p7zip-full sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# --- Python: Office / PDF / NER (hash-locked) -------------------------------------
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    VIRTUAL_ENV=/opt/office-py \
    PATH=/opt/office-py/bin:$PATH
COPY files/requirements.lock /tmp/requirements.lock
RUN uv venv --seed --python 3.12 /opt/office-py \
    && uv pip install --python /opt/office-py/bin/python --require-hashes -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock

# --- Pi coding agent: standalone binary (Bun-compiled, deps bundled at release) --
ARG PI_VERSION=1.5.2
ARG PI_SHA256_ARM64=42805cd5615952fcbbfc9c56b04f8feb84395134952e169a6cdc8d7676975666
ARG PI_SHA256_AMD64=811c6f2c9e7aaee252b5404252e556fb713aa209c1f7f924226aa77d4ef70cb5
ARG TARGETARCH
RUN set -eu; \
    case "$TARGETARCH" in \
      arm64) arch=arm64; sum="$PI_SHA256_ARM64" ;; \
      amd64) arch=x64;   sum="$PI_SHA256_AMD64" ;; \
      *) echo "unsupported arch: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/pi.tgz \
      "https://github.com/earendil-works/pi/releases/download/v${PI_VERSION}/pi-linux-${arch}.tar.gz"; \
    echo "${sum}  /tmp/pi.tgz" | sha256sum -c -; \
    mkdir -p /opt && tar xzf /tmp/pi.tgz -C /opt && rm /tmp/pi.tgz; \
    chown -R root:root /opt/pi; \
    ln -s /opt/pi/pi /usr/local/bin/pi; \
    pi --version

# Warm LibreOffice's user profile once so the first headless run is quick.
RUN su agent -c 'cd /tmp && echo x > warm.txt && soffice --headless --convert-to pdf warm.txt >/dev/null 2>&1; rm -f warm.*' || true

# --- hush-anon: deterministic anonymisation (extract / propose / apply / check)
COPY --chmod=755 files/hush-anon /usr/local/bin/hush-anon

# --- Offline-friendly environment ----------------------------------------------
# sbx launches the agent through non-interactive bash, which reads BASH_ENV.
COPY files/sandbox.env /etc/hush-sandbox.env
RUN cat /etc/hush-sandbox.env >> /etc/sandbox-persistent.sh
ENV BASH_ENV=/etc/hush-sandbox.env \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    PI_OFFLINE=1 PI_SKIP_VERSION_CHECK=1 PI_TELEMETRY=0

COPY --chown=agent:agent files/pi/models.json files/pi/settings.json files/AGENTS.md /home/agent/.pi/agent/
# sessions/ is replaced at run time by a per-folder host mount (see hush).
RUN mkdir -p /home/agent/.pi/agent/sessions && chown -R agent:agent /home/agent/.pi

USER agent
# sbx injects the host's GitHub token (GH_TOKEN) into every sandbox; strip it
# and the SSH agent socket before Pi starts.
ENTRYPOINT ["env", "-u", "GH_TOKEN", "-u", "GITHUB_TOKEN", "-u", "SSH_AUTH_SOCK", "pi"]
CMD []
