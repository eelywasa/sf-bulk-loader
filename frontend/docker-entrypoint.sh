#!/bin/sh
set -eu

# Selects the active nginx config at container start based on NGINX_HTTPS.
# Both configs are baked into the image under /etc/nginx/templates/ so the
# image is fully self-contained — no bind-mount required for HTTPS.

if [ "${NGINX_HTTPS:-false}" = "true" ]; then
    cp /etc/nginx/templates/https.conf /etc/nginx/conf.d/app.conf
else
    cp /etc/nginx/templates/http.conf /etc/nginx/conf.d/app.conf
fi

exec nginx -g 'daemon off;'
