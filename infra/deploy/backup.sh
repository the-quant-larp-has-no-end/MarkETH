#!/bin/sh
# Dump the Postgres database of the deployed stack into a file beside its tree.
#
#   ssh <vm> 'sh -s -- <directory> <volume>' < infra/deploy/backup.sh
#
# <directory> is the tree of the stack, relative to the home directory; <volume> is the
# Docker volume that holds the database. `make deploy-backup` runs it this way, from the
# laptop's copy, so it works before the tree has ever been sent. `make deploy` and
# `make deploy-wipe` both start with it.
#
# Without the volume there is no database and nothing to do. With it, the dump goes to
# ~/<directory>-backups/<UTC time>.dump in pg_dump's custom format, readable by its
# owner only. Restore one into an empty database with:
#
#   docker compose -f compose.yaml exec -T postgres \
#     sh -c 'exec pg_restore --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' < <file>
#
# Balances are redeemable for drinks: a failed dump stops whatever asked for it.

set -eu

# The script arrives on standard input. Everything is one function, so that the shell has
# read all of it before a command in it could read standard input too.
main() {
    if [ "$#" -ne 2 ]; then
        echo "usage: sh -s -- <directory> <volume> < backup.sh" >&2
        exit 2
    fi
    directory=$1
    volume=$2
    tree="$HOME/$directory"
    backups="$HOME/$directory-backups"

    if [ -z "$(docker volume ls --quiet --filter "name=^$volume\$" < /dev/null)" ]; then
        echo "No database volume ($volume): nothing to back up."
        exit 0
    fi
    if [ ! -f "$tree/compose.yaml" ] || [ ! -f "$tree/.env" ]; then
        echo "The volume $volume exists, but $tree has no compose.yaml and .env to reach it with." >&2
        echo "Nothing was dumped and nothing was changed." >&2
        exit 1
    fi
    cd "$tree"

    if [ -z "$(docker compose -f compose.yaml ps --quiet --status running postgres < /dev/null)" ]; then
        echo "Postgres is not running: starting it alone for the dump."
        docker compose -f compose.yaml up --detach --wait --no-deps postgres < /dev/null
    fi

    umask 077
    mkdir -p "$backups"
    target="$backups/$(date -u +%Y%m%dT%H%M%SZ).dump"
    trap 'rm -f "$target.partial"' EXIT
    # shellcheck disable=SC2016  # the variables are the container's, not this shell's
    docker compose -f compose.yaml exec -T postgres \
        sh -c 'exec pg_dump --format=custom --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
        < /dev/null > "$target.partial"
    if [ ! -s "$target.partial" ]; then
        echo "pg_dump wrote nothing. No dump was kept." >&2
        exit 1
    fi
    mv "$target.partial" "$target"
    echo "Database dumped to $target ($(wc -c < "$target" | tr -d ' ') bytes)."
}

main "$@"
