FROM node:20-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine

COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

# Set these at deploy time to point at your gateway, e.g.:
#   GATEWAY_URL=https://<gateway-fqdn>/api/
#   GATEWAY_HOST=<gateway-fqdn>
ENV GATEWAY_URL=http://localhost:8080/api/
ENV GATEWAY_HOST=localhost

EXPOSE 80
