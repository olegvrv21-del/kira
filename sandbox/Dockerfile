FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

RUN apt-get update && apt-get install -y --no-install-recommends \
      ripgrep jq tree less file unzip python3-pip curl ca-certificates gnupg \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --break-system-packages --no-cache-dir \
      playwright==1.49.1 fastapi==0.115.5 uvicorn==0.32.1

# Install Node.js 22 (with npm) from NodeSource — playwright ships node binary
# only, no npm. We need npm/npx for pyright + typescript-language-server.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && node --version && npm --version

# LSP servers + tsc for type-checking JS/TS projects.
RUN npm install -g --omit=dev \
      pyright@1.1.390 \
      typescript@5.6.3 \
      typescript-language-server@4.3.3 \
 && pyright --version && typescript-language-server --version

# Keep /usr/local/bin/node as well for back-compat with earlier image scripts.
RUN ln -sf /usr/bin/node /usr/local/bin/node

# Copy daemons and entrypoint
COPY --chmod=755 browser_daemon.py /opt/browser_daemon.py
COPY --chmod=644 _eval_wrap.py /opt/_eval_wrap.py
COPY --chmod=755 lsp_daemon.py /opt/lsp_daemon.py
COPY --chmod=755 entrypoint.sh /opt/entrypoint.sh

RUN mkdir /workspace && chown 1000:1000 /workspace
USER 1000
WORKDIR /workspace

CMD ["/opt/entrypoint.sh"]
