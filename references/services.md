# Backing services (Compose snippets)

`detect.py` reports `backing_services` from real dependency names, so only add what it lists (plus anything the user names). Every snippet:

- pins the image
- has a **real readiness healthcheck**, so the app can use `depends_on: condition: service_healthy`
- puts data on a **named volume at the correct data dir**
- takes credentials from `.env` via `${VAR:?message}`, which fails fast if the variable is unset
- publishes **no host port**. The app reaches it on the compose network as `<service>:<port>`. For local GUI tools, add a dev override with `127.0.0.1:5432:5432`.

Put the shared blocks at the top of `compose.yaml`:

```yaml
x-logging: &logging
  driver: json-file
  options: { max-size: "10m", max-file: "3" }
x-restart: &restart
  restart: unless-stopped
```

## PostgreSQL

```yaml
  db:
    image: postgres:18-alpine
    <<: *restart
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-app}
      POSTGRES_USER: ${POSTGRES_USER:-app}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
    volumes:
      - pg_data:/var/lib/postgresql        # 18+: NOT /var/lib/postgresql/data (audit VS-C061)
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 10s
    shm_size: 256mb
    deploy: { resources: { limits: { memory: 512M } } }
    logging: *logging
```

App env: `DATABASE_URL=postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}`. Use `postgis/postgis:18-3.6-alpine` for PostGIS and `pgvector/pgvector:pg18` for vectors. Upgrading 17→18 needs `pg_upgrade` or dump/restore. Never just bump the tag on an existing volume.

## MySQL / MariaDB

```yaml
  db:
    image: mysql:8.4
    <<: *restart
    environment:
      MYSQL_DATABASE: ${DB_NAME:-app}
      MYSQL_USER: ${DB_USER:-app}
      MYSQL_PASSWORD: ${DB_PASSWORD:?set DB_PASSWORD}
      MYSQL_ROOT_PASSWORD: ${DB_ROOT_PASSWORD:?set DB_ROOT_PASSWORD}
    volumes: [mysql_data:/var/lib/mysql]
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1", "--silent"]
      interval: 5s
      retries: 20
      start_period: 30s
    logging: *logging
```

MariaDB: `image: mariadb:11.8`, same env with the `MARIADB_` prefix, healthcheck `["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]`.

## MongoDB

```yaml
  mongo:
    image: mongo:8
    <<: *restart
    environment:
      MONGO_INITDB_ROOT_USERNAME: ${MONGO_USER:-app}
      MONGO_INITDB_ROOT_PASSWORD: ${MONGO_PASSWORD:?set MONGO_PASSWORD}
    volumes: [mongo_data:/data/db]
    healthcheck:
      test: ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok"]
      interval: 10s
      retries: 10
      start_period: 20s
    logging: *logging
```

URI: `mongodb://${MONGO_USER}:${MONGO_PASSWORD}@mongo:27017/?authSource=admin`.

## Redis / Valkey

```yaml
  redis:
    image: redis:8-alpine            # or valkey/valkey:8-alpine (BSD-licensed fork, drop-in)
    <<: *restart
    command: ["redis-server", "--appendonly", "yes", "--requirepass", "${REDIS_PASSWORD:?set REDIS_PASSWORD}"]
    volumes: [redis_data:/data]
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a \"$$REDIS_PASSWORD\" --no-auth-warning ping | grep -q PONG"]
      interval: 5s
      retries: 10
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD}
    deploy: { resources: { limits: { memory: 256M } } }
    logging: *logging
```

For a pure cache, drop `--appendonly` and the volume, and add `--maxmemory 256mb --maxmemory-policy allkeys-lru`. URL: `redis://:${REDIS_PASSWORD}@redis:6379/0`.

## RabbitMQ

```yaml
  rabbitmq:
    image: rabbitmq:4-management-alpine
    <<: *restart
    hostname: rabbitmq                 # data dir is keyed by node name - keep it stable
    environment:
      RABBITMQ_DEFAULT_USER: ${RABBITMQ_USER:-app}
      RABBITMQ_DEFAULT_PASS: ${RABBITMQ_PASSWORD:?set RABBITMQ_PASSWORD}
    volumes: [rabbitmq_data:/var/lib/rabbitmq]
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"]
      interval: 10s
      retries: 10
      start_period: 20s
    logging: *logging
```

## Kafka (single-node KRaft, no ZooKeeper)

```yaml
  kafka:
    image: apache/kafka:4.1.0
    <<: *restart
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      KAFKA_LOG_DIRS: /var/lib/kafka/data
    volumes: [kafka_data:/var/lib/kafka/data]
    healthcheck:
      test: ["CMD-SHELL", "/opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server localhost:9092 >/dev/null 2>&1"]
      interval: 10s
      retries: 15
      start_period: 30s
    logging: *logging
```

A single broker is fine for dev and small production. For real durability, use 3 brokers or a managed service (MSK, Confluent, Redpanda Cloud). Say so in DEPLOYMENT.md.

## Elasticsearch / OpenSearch

```yaml
  elasticsearch:
    image: elasticsearch:9.1.0
    <<: *restart
    environment:
      discovery.type: single-node
      xpack.security.enabled: "true"
      ELASTIC_PASSWORD: ${ELASTIC_PASSWORD:?set ELASTIC_PASSWORD}
      ES_JAVA_OPTS: -Xms512m -Xmx512m
    volumes: [es_data:/usr/share/elasticsearch/data]
    ulimits: { memlock: { soft: -1, hard: -1 } }
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS -u elastic:$$ELASTIC_PASSWORD http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=5s"]
      interval: 15s
      retries: 20
      start_period: 60s
    deploy: { resources: { limits: { memory: 1G } } }
    logging: *logging
```

The host needs `vm.max_map_count=262144` (note it in DEPLOYMENT.md). OpenSearch: `opensearchproject/opensearch:3`, `OPENSEARCH_INITIAL_ADMIN_PASSWORD`, data at `/usr/share/opensearch/data`.

## Others

| Service | Image | Healthcheck | Volume |
|---|---|---|---|
| Memcached | `memcached:1.6-alpine` | none (or a TCP check from the app) | none |
| NATS (JetStream) | `nats:2-alpine`, `command: ["-js", "-sd", "/data"]` | `["CMD", "wget", "-qO-", "http://localhost:8222/healthz"]` with `-m 8222` | `nats_data:/data` |
| ClickHouse | `clickhouse/clickhouse-server:25.8` | `["CMD", "clickhouse-client", "--query", "SELECT 1"]` | `ch_data:/var/lib/clickhouse` |
| Meilisearch | `getmeili/meilisearch:v1.16`, `MEILI_MASTER_KEY` | `["CMD", "curl", "-f", "http://localhost:7700/health"]` | `meili_data:/meili_data` |
| Qdrant | `qdrant/qdrant:v1.15.0` | TCP via app (no curl in image) | `qdrant_data:/qdrant/storage` |
| Mailpit (dev only) | `axllent/mailpit:v1.27` | built-in | none. SMTP `mailpit:1025`, UI 8025 bound to 127.0.0.1 |
| S3 (dev) | `chrislusf/seaweedfs` `server -s3` or `localstack/localstack:4` | per docs | `s3_data:/data` |

## One-off jobs (migrations, seeds)

```yaml
  migrate:
    build: .
    command: ["python", "manage.py", "migrate", "--noinput"]   # stack-specific, see stacks/*.md
    env_file: .env
    depends_on:
      db: { condition: service_healthy }
    restart: "no"

  app:
    depends_on:
      db: { condition: service_healthy }
      migrate: { condition: service_completed_successfully }
```

## Volumes block

```yaml
volumes:
  pg_data:
  redis_data:
```

Backups are not automatic. Add a DEPLOYMENT.md checklist item: `docker compose exec db pg_dump -U app app | gzip > backup-$(date +%F).sql.gz` on a cron, or use a managed database.
