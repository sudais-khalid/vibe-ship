FROM node:latest
MAINTAINER someone@example.com
ARG NPM_TOKEN
ENV API_KEY=AKIAIOSFODNN7EXAMPLE
WORKDIR /app
COPY . .
COPY .env /app/.env
RUN apt-get update && apt-get install -y curl
RUN curl -fsSL https://example.com/install.sh | bash
RUN npm install && npm run build
RUN chmod -R 777 /app
EXPOSE 3000 22
CMD npm start
