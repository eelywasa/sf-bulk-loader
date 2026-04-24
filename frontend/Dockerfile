# ---- Build stage ----
FROM node:20-alpine AS builder

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Copy frontend source and the usage docs the help-content plugin needs at build time
COPY frontend/ .
COPY docs/usage/ /docs/usage/
RUN npm run build

# ---- Serve stage ----
FROM nginx:1.27-alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Bake both HTTP and HTTPS configs into the image as templates. The entrypoint
# selects the active one at container start based on NGINX_HTTPS.
COPY frontend/nginx.conf       /etc/nginx/templates/http.conf
COPY frontend/nginx.https.conf /etc/nginx/templates/https.conf

COPY frontend/docker-entrypoint.sh /docker-entrypoint-sfbl.sh
RUN chmod +x /docker-entrypoint-sfbl.sh

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
EXPOSE 443

ENTRYPOINT ["/docker-entrypoint-sfbl.sh"]
