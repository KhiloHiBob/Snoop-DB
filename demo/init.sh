#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 --set=reader_password="$SNOOP_DB_PASSWORD" \
  --file=/demo/sample.sql
