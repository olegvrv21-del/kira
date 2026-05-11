FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

RUN apt-get update && apt-get install -y --no-install-recommends \
      ripgrep jq tree less file unzip python3-pip \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --break-system-packages --no-cache-dir \
      playwright==1.49.1 fastapi==0.115.5 uvicorn==0.32.1

# Expose playwright's bundled Node in PATH for JS linting (node --check).
RUN ln -sf /usr/local/lib/python3.12/dist-packages/playwright/driver/node /usr/local/bin/node \
 && /usr/local/bin/node --version

# Copy browser daemon and entrypoint
COPY --chmod=755 browser_daemon.py /opt/browser_daemon.py
COPY --chmod=755 entrypoint.sh /opt/entrypoint.sh

RUN mkdir /workspace && chown 1000:1000 /workspace
USER 1000
WORKDIR /workspace

CMD ["/opt/entrypoint.sh"]
