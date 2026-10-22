# JVM: Java, Kotlin, Scala, Clojure

Covers Spring Boot, Quarkus, Micronaut, Ktor, Javalin, Vert.x, Dropwizard, Helidon, Play (sbt), and Ring (Leiningen).

## Universal JVM rules

- Runtime image is a **JRE**, not a JDK: `eclipse-temurin:<v>-jre-alpine` (or `-jre` on Ubuntu if native libs need glibc).
- Memory: `JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75 -XX:+ExitOnOutOfMemoryError"`. The JVM is container-aware but defaults to only 25% of the limit for heap (audit `VS-D084`). `ExitOnOutOfMemoryError` makes the orchestrator restart a broken JVM instead of letting it limp along.
- Use the build wrapper (`./mvnw`, `./gradlew`) when it exists so the build tool version matches the project. Dependency caching uses `--mount=type=cache,target=/root/.m2` or `/root/.gradle`.
- Java version comes from `detect.py` (`<java.version>`, toolchain). If it's missing, use 25.

## Spring Boot: Maven (layered, Boot ≥ 3.3)

```dockerfile
# syntax=docker/dockerfile:1
ARG JAVA_VERSION=25
FROM eclipse-temurin:${JAVA_VERSION}-jdk AS build
WORKDIR /src
COPY mvnw pom.xml ./
COPY .mvn .mvn
RUN --mount=type=cache,target=/root/.m2 ./mvnw -B -q dependency:go-offline
COPY src src
RUN --mount=type=cache,target=/root/.m2 ./mvnw -B -q package -DskipTests \
 && cp target/*.jar /tmp/app.jar \
 && java -Djarmode=tools -jar /tmp/app.jar extract --layers --launcher --destination /tmp/extracted

FROM eclipse-temurin:${JAVA_VERSION}-jre-alpine AS runtime
RUN addgroup -S app && adduser -S -u 10001 -G app app
WORKDIR /app
COPY --from=build /tmp/extracted/dependencies/ ./
COPY --from=build /tmp/extracted/spring-boot-loader/ ./
COPY --from=build /tmp/extracted/snapshot-dependencies/ ./
COPY --from=build /tmp/extracted/application/ ./
USER app
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75 -XX:+ExitOnOutOfMemoryError" SERVER_PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/actuator/health >/dev/null || exit 1
ENTRYPOINT ["java", "org.springframework.boot.loader.launch.JarLauncher"]
```

- Layers mean a code change only rebuilds the small `application/` layer, and the dependencies layer stays cached and shared.
- **Boot < 3.3:** extract with `java -Djarmode=layertools -jar /tmp/app.jar extract --destination /tmp/extracted` (same four dirs). Boot 3.0–3.1 uses `org.springframework.boot.loader.JarLauncher`.
- **No mvnw:** use `FROM maven:3.9-eclipse-temurin-25 AS build` and `mvn` instead of `./mvnw`.
- **Multi-module:** build with `-pl <module> -am` and copy `<module>/target/*.jar`.
- **Health:** `/actuator/health` needs `spring-boot-starter-actuator` (`detect.py` reports `actuator: true/false`). On K8s, enable probes with `management.endpoint.health.probes.enabled=true` and use `/actuator/health/readiness` and `/actuator/health/liveness`.
- `start-period=60s`, because Spring often takes 10–40s to start. Kubernetes uses a `startupProbe` (see `kubernetes.md`).

## Spring Boot: Gradle

```dockerfile
FROM eclipse-temurin:25-jdk AS build
WORKDIR /src
COPY gradlew settings.gradle* build.gradle* gradle.properties* ./
COPY gradle gradle
RUN --mount=type=cache,target=/root/.gradle ./gradlew --no-daemon dependencies > /dev/null
COPY src src
RUN --mount=type=cache,target=/root/.gradle ./gradlew --no-daemon bootJar -x test \
 && cp $(ls build/libs/*.jar | grep -v -- '-plain.jar') /tmp/app.jar \
 && java -Djarmode=tools -jar /tmp/app.jar extract --layers --launcher --destination /tmp/extracted
# runtime stage identical to the Maven one
```

## Quarkus (fast-jar)

```dockerfile
FROM eclipse-temurin:25-jdk AS build
WORKDIR /src
COPY mvnw pom.xml ./
COPY .mvn .mvn
RUN --mount=type=cache,target=/root/.m2 ./mvnw -B -q dependency:go-offline
COPY src src
RUN --mount=type=cache,target=/root/.m2 ./mvnw -B -q package -DskipTests

FROM eclipse-temurin:25-jre-alpine AS runtime
RUN adduser -D -u 10001 app
WORKDIR /app
COPY --from=build --chown=app /src/target/quarkus-app/lib/ ./lib/
COPY --from=build --chown=app /src/target/quarkus-app/*.jar ./
COPY --from=build --chown=app /src/target/quarkus-app/app/ ./app/
COPY --from=build --chown=app /src/target/quarkus-app/quarkus/ ./quarkus/
USER app
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75 -Dquarkus.http.host=0.0.0.0"
EXPOSE 8080
HEALTHCHECK CMD wget -qO- http://127.0.0.1:8080/q/health/ready >/dev/null || exit 1
ENTRYPOINT ["java", "-jar", "quarkus-run.jar"]
```

Native (GraalVM / Mandrel) images build in about 5–10 minutes and start in milliseconds. Offer native only when the user wants scale-to-zero (Cloud Run, Knative, Lambda).

## Micronaut / Ktor / Javalin / Vert.x / Dropwizard / Helidon

These build a fat/shadow jar: Micronaut `./gradlew shadowJar` or `mvn package`, Ktor `./gradlew buildFatJar`. Copy the single jar and run `ENTRYPOINT ["java", "-jar", "/app/app.jar"]` with the same JRE runtime, user, and memory flags. Ktor binds `0.0.0.0` by default in `application.conf`. Verify `ktor.deployment.host`.

## Scala (sbt / Play) and Clojure

- **sbt:** `FROM sbtscala/scala-sbt:<jdk>_<sbt>_<scala> AS build`, then `sbt -batch stage` (sbt-native-packager) and copy `target/universal/stage/` into a JRE runtime. Play listens on 9000 and needs `-Dplay.http.secret.key` from env. Play writes `RUNNING_PID`, so set `-Dpidfile.path=/dev/null`.
- **Leiningen:** `FROM clojure:temurin-25-lein AS build` → `lein uberjar` → copy `target/uberjar/*-standalone.jar`. **tools.deps:** `clojure -T:build uber`.

## Buildpacks alternative

`./mvnw spring-boot:build-image` / `./gradlew bootBuildImage` produce a good OCI image with no Dockerfile, using Paketo, non-root by default. Mention it as an option. Vibe-ship still writes the Dockerfile so the build stays explicit and auditable.
