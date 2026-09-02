# Multi-stage: build the static bundle with Node, then serve it with nginx —
# the production container never needs Node or the source tree, just the
# built dist/ output.
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# import.meta.env.DEV is false in this build (Vite's own production-mode
# flag), so the DEV badge and dev-mode API port never ship here — this is
# always the production build, matching nginx.conf's port 5190.
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 5190
