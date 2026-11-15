FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY app ./app
COPY lib ./lib
COPY public ./public
COPY next.config.ts tsconfig.json ./
ENV FINOPS_API_ORIGIN=http://api:8000 NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev

FROM node:24-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app /app
USER node
CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0"]
