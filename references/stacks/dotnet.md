# .NET (ASP.NET Core, Worker Service, F#)

`detect.py` reports `project_file`, `assembly`, `runtime_version` (from `<TargetFramework>`), and whether it's web (`Microsoft.NET.Sdk.Web`) or a worker.

## Template

```dockerfile
# syntax=docker/dockerfile:1
ARG DOTNET_VERSION=10.0
FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:${DOTNET_VERSION} AS build
ARG TARGETARCH
WORKDIR /src
# Copy project files first for restore caching. For solutions, copy every *.csproj keeping its folder.
COPY Api.csproj ./
RUN --mount=type=cache,target=/root/.nuget/packages dotnet restore -a $TARGETARCH
COPY . .
RUN --mount=type=cache,target=/root/.nuget/packages \
    dotnet publish Api.csproj -c Release -a $TARGETARCH --no-restore -o /out /p:UseAppHost=false

# Runtime A (Compose/VPS, keeps a shell + wget for HEALTHCHECK)
FROM mcr.microsoft.com/dotnet/aspnet:${DOTNET_VERSION}-alpine AS runtime
WORKDIR /app
COPY --from=build /out ./
USER $APP_UID
ENV ASPNETCORE_HTTP_PORTS=8080 DOTNET_RUNNING_IN_CONTAINER=true
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
ENTRYPOINT ["dotnet", "Api.dll"]
```

**Runtime B (Kubernetes / Cloud Run):** `mcr.microsoft.com/dotnet/aspnet:10.0-noble-chiseled` has no shell or package manager and is non-root by default. Drop the HEALTHCHECK (use `# vibe-ship: ignore VS-D022` with the reason) and rely on platform probes.

## Notes

- .NET 8+ images listen on **8080** and define `APP_UID` (1654) for the built-in non-root `app` user. Don't create another user.
- **Alpine + globalization:** alpine aspnet images run in invariant mode by default. If the app needs cultures (date/number formatting, `CultureInfo("de-DE")`), add `RUN apk add --no-cache icu-libs` and `ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=false`, or use the Debian/Ubuntu image.
- **Health:** add `builder.Services.AddHealthChecks()` + `app.MapHealthChecks("/healthz")`, but only with the user's approval since it's an app change.
- **Solutions:** restore needs every referenced `*.csproj` at its relative path. Copy them individually (`COPY src/Api/Api.csproj src/Api/`), or `COPY . .` before restore and accept slower caching.
- **EF Core migrations:** generate an idempotent bundle at build time (`dotnet ef migrations bundle -o /out/efbundle`) and run it as a one-off service or job. Never call `Database.Migrate()` at startup with more than one replica.
- **Worker services** (`Microsoft.NET.Sdk.Worker`): runtime image `mcr.microsoft.com/dotnet/runtime:10.0-alpine`, no EXPOSE, no HTTP healthcheck.
- **Native AOT** (`<PublishAot>true</PublishAot>`): build on `sdk:10.0-aot` and run on `runtime-deps:10.0-noble-chiseled`. Startup is in milliseconds. Use it only if the project already enables AOT.
