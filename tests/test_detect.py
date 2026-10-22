"""Regression tests for scripts/detect.py. Run: python -m unittest discover -s tests"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import detect  # noqa: E402

STACKS = os.path.join(HERE, "fixtures", "stacks")


def svc(name, path="."):
    r = detect.detect(os.path.join(STACKS, name))
    for s in r["services"]:
        if s["path"] == path:
            return s, r
    raise AssertionError("no service at %s in %s: %s" % (path, name, [s["path"] for s in r["services"]]))


class DetectTests(unittest.TestCase):
    def check(self, name, path=".", **expected):
        s, _ = svc(name, path)
        for k, v in expected.items():
            self.assertEqual(s.get(k), v, "%s/%s: %s=%r, expected %r" % (name, path, k, s.get(k), v))
        return s

    def test_nextjs(self):
        s = self.check("next-app", framework="nextjs", package_manager="pnpm", language="typescript",
                       runtime_version="22.11", port=3000, health_path="/healthz", next_standalone=True)
        self.assertIn("postgres", s["backing_services"])
        self.assertIn("STRIPE_SECRET_KEY", s["env_vars"])

    def test_fastapi(self):
        s = self.check("fastapi-app", framework="fastapi", package_manager="uv", runtime_version="3.13",
                       entrypoint="app.main:app", health_path="/healthz")
        self.assertEqual(s["backing_services"], ["postgres", "redis"])
        self.assertEqual(s["workers"], ["celery"])
        self.assertTrue({"DATABASE_URL", "JWT_SECRET"} <= set(s["env_vars"]))

    def test_django(self):
        self.check("django-app", framework="django", entrypoint="mysite.wsgi:application",
                   migrations="python manage.py migrate --noinput", backing_services=["postgres"])

    def test_go(self):
        self.check("go-app", framework="gin", runtime_version="1.25.1", entrypoint="./cmd/server", port=9090,
                   backing_services=["postgres"])

    def test_rails(self):
        s = self.check("rails-app", framework="rails", runtime_version="3.4.1", health_path="/up",
                       backing_services=["postgres", "redis"], workers=["sidekiq"])
        self.assertIn("asset_build", s)

    def test_laravel(self):
        self.check("laravel-app", framework="laravel", runtime_version="8.3", backing_services=["postgres", "redis"],
                   migrations="php artisan migrate --force")

    def test_spring(self):
        self.check("spring-app", framework="spring-boot", package_manager="maven", runtime_version="21",
                   health_path="/actuator/health", backing_services=["postgres"])

    def test_dotnet(self):
        self.check("dotnet-app", framework="aspnetcore", runtime_version="10.0", port=8080, backing_services=["postgres"])

    def test_rust(self):
        self.check("rust-app", framework="axum", runtime_version="1.88", binary="svc")

    def test_phoenix(self):
        self.check("phoenix-app", framework="phoenix", runtime_version="1.19.0", port=4000, backing_services=["postgres"])

    def test_vite_static(self):
        self.check("vite-app", framework="vite", kind="static", static_out="dist", port=8080)

    def test_monorepo(self):
        r = detect.detect(os.path.join(STACKS, "monorepo"))
        paths = sorted(s["path"] for s in r["services"])
        self.assertEqual(paths, ["apps/api", "apps/web"])
        self.assertEqual(r["monorepo"]["tool"], "turbo")
        self.assertIn("packages/ui", [p["path"] for p in r["packages"]])
        api = next(s for s in r["services"] if s["path"] == "apps/api")
        self.assertEqual(api["framework"], "nestjs")
        self.assertEqual(api["backing_services"], ["redis"])


if __name__ == "__main__":
    unittest.main()
