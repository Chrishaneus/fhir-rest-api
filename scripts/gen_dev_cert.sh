#!/usr/bin/env sh
# Generate a self-signed certificate for local HTTPS development.
# Output: nginx/certs/server.crt and nginx/certs/server.key
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERT_DIR="$SCRIPT_DIR/../nginx/certs"

mkdir -p "$CERT_DIR"

# Write a temp config so subjectAltName works across all OpenSSL versions
# (-addext is only available in OpenSSL 1.1.1+).
CONF=$(mktemp)
cat > "$CONF" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions    = v3_req
prompt             = no

[req_distinguished_name]
CN = localhost

[v3_req]
subjectAltName = DNS:localhost,IP:127.0.0.1
EOF

openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
  -keyout "$CERT_DIR/server.key" \
  -out    "$CERT_DIR/server.crt" \
  -config "$CONF"

rm -f "$CONF"

echo "Certificate written to $CERT_DIR"
echo "Trust it in your OS/browser to silence the self-signed warning."
