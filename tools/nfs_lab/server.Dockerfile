FROM debian:bookworm-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       nfs-kernel-server \
    && rm -rf /var/lib/apt/lists/*

COPY tools/nfs_lab/server-entrypoint.sh /usr/local/bin/server-entrypoint
RUN chmod 0755 /usr/local/bin/server-entrypoint

ENTRYPOINT ["/usr/local/bin/server-entrypoint"]
