#!/bin/sh
set -eu

mkdir -p /exports /run/rpc_pipefs /var/lib/nfs/rpc_pipefs
chmod 0777 /exports
mountpoint -q /proc/fs/nfsd || mount -t nfsd nfsd /proc/fs/nfsd

printf '/exports *(rw,sync,no_subtree_check,no_root_squash,fsid=0,insecure)\n' > /etc/exports
rpcbind
rpc.statd
exportfs -rav
rpc.nfsd --no-udp --lease-time 10 --grace-time 5 8
exec rpc.mountd --no-udp --foreground
