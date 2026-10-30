FROM node:22-alpine AS build
WORKDIR /web
COPY package*.json ./
RUN npm ci
COPY . .
# BACKEND_URL is baked into the rewrite at build time; override with --build-arg for non-compose setups
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=$BACKEND_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /web
ENV NODE_ENV=production
COPY --from=build /web ./
USER node
EXPOSE 3000
CMD ["npm", "start"]
