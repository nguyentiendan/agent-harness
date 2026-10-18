FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 bash coreutils git ca-certificates && rm -rf /var/lib/apt/lists/* \
    && mkdir /workspace && chown 10001:10001 /workspace
COPY worker.py /opt/worker.py
USER 10001:10001
WORKDIR /workspace
ENV HOME=/tmp
CMD ["sleep", "900"]
