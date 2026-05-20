# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Django reusable app that integrates Keycloak as an identity provider. Distributed via `setup.py` (package name `django-keycloak`), installed into a host Django project. The package lives under `src/django_keycloak/` (src layout). The `example/` directory is a self-contained Docker Compose showcase, not part of the published package.

This is a fork of `Peter-Slump/django-keycloak`. Local divergence from upstream so far (see `git log`):
- Multi-realm support: requests pick the realm from a `realm` HTTP header, falling back to the first `Realm` in the DB (`middleware.py:14-20`).
- `iss` (issuer) verification disabled when decoding ID tokens so multiple domains can share one realm (`services/oidc_profile.py:72`, `options={"verify_iss": False}`).

When changing realm selection or token verification, preserve those two behaviours unless the task is explicitly to undo them.

## Commands

Install for development (editable + dev/doc extras):

```bash
make install-python
```

Run the full test suite (uses `pytest-django`; settings module `django_keycloak.tests.settings`, in-memory sqlite):

```bash
python setup.py test
# or directly
pytest
```

Single test file / single test:

```bash
pytest src/django_keycloak/tests/services/oidc_profile/test_update_or_create.py
pytest src/django_keycloak/tests/services/oidc_profile/test_update_or_create.py::ClassName::test_name
```

Coverage (matches CI invocation):

```bash
python setup.py test --addopts "--cov=django_keycloak --cov-report xml:coverage.xml"
```

Lint (CI runs this against `src/`):

```bash
flake8 ./src
```

Docs (Sphinx, live-reload via Docker):

```bash
docker build . -f DockerfileDocs -t django-keycloak-docs
docker run -v `pwd`:/src --rm -t -i -p 8050:8050 django-keycloak-docs
# http://localhost:8050
```

Example project (full Keycloak + two Django apps behind nginx with self-signed TLS):

```bash
cd example && docker-compose up
# https://resource-provider.localhost.yarf.nl/
```

## Architecture

### Request lifecycle

1. `BaseKeycloakMiddleware` attaches `request.realm` as a `SimpleLazyObject`. Realm is resolved from the `realm` request header, else the first `Realm` row (`middleware.py:14`). Downstream code assumes `request.realm` always exists — any new auth backend / view must be reached through this middleware.
2. An auth backend in `auth/backends.py` is invoked via `django.contrib.auth.authenticate(...)`. Three backends exist, distinguished by the credentials they accept:
   - `KeycloakAuthorizationCodeBackend` — OAuth2 auth-code flow (used by the `Login`/`LoginComplete` views in `views.py`).
   - `KeycloakPasswordCredentialsBackend` — resource owner password credentials.
   - `KeycloakIDTokenAuthorizationBackend` — bearer token from `Authorization` header (paired with `KeycloakStatelessBearerAuthenticationMiddleware`).
3. Each backend delegates to `services.oidc_profile` to exchange the credential, decode the token, and upsert the user + `OpenIdConnectProfile`.
4. `BaseKeycloakMiddleware.process_response` writes the Keycloak `session_state` to a non-HttpOnly cookie so the browser-side session iframe (`views.SessionIframe`) can detect SSO sign-outs.

### Models (`models.py`)

`Server` → has many `Realm` → has one `Client` (the Django app's OIDC client) and many `OpenIdConnectProfile` (one per logged-in identity). Profiles store the `access_token` / `refresh_token` and their expiries (`TokenModelAbstract`).

`Server.internal_url` is the in-cluster URL Django uses to call Keycloak; `Server.url` is the public URL the browser sees. `views.Login` and `views.SessionIframe` rewrite URLs from internal → public before sending them to the browser (`views.py:53-58`, `views.py:130-136`). Preserve this rewrite whenever you touch redirect URLs.

`OpenIdConnectProfile` is **swappable** via `settings.KEYCLOAK_OIDC_PROFILE_MODEL`. Two concrete implementations:
- `OpenIdConnectProfile` (default): FK to `AUTH_USER_MODEL`. A Django `User` row is created/updated on every login.
- `RemoteUserOpenIdConnectProfile`: no local `User` row. The "user" is `KeycloakRemoteUser` (`remote_user.py`), a plain Python object hydrated from `/userinfo` on demand. Auth state is kept in the session under a separate `REMOTE_SESSION_KEY` to avoid collisions with `django.contrib.auth`.

Code that wants the active profile model must call `services.oidc_profile.get_openid_connect_profile_model()` rather than importing the model directly. The two profile classes have an `is_remote` class attribute that branches behaviour throughout `services/oidc_profile.py`.

### Services layer (`services/`)

All Keycloak HTTP calls go through `python-keycloak-client`, never `requests` directly. The `Realm`/`Client` models expose `@cached_property` accessors (`realm_api_client`, `admin_api_client`, `openid_api_client`, `authz_api_client`, `uma1_api_client`) that build configured clients lazily — use those instead of constructing clients yourself.

`services.client.get_service_account_profile` lazily provisions a service-account `OpenIdConnectProfile` for a `Client` and caches it on `Client.service_account_profile`. Admin API calls require this; if you add a new admin operation, route the token through `get_access_token(client)` so refresh-on-expiry works.

Token refresh is centralized in `services.oidc_profile.get_active_access_token` — it raises `TokensExpired` when the refresh token itself has expired. New code that touches tokens should call this rather than reading `access_token` off the model directly.

### Permissions

Two strategies, selected by `settings.KEYCLOAK_PERMISSIONS_METHOD` (`backends.py:51-83`):
- `'role'`: permissions are role names under `resource_access[client_id].roles` in the RPT.
- `'resource'`: permissions are derived from UMA resources + scopes, formatted as `<app>.<scope>_<model>` (matching Django's permission codename convention) when the resource name contains a dot.

The entitlement flow differs between Keycloak versions: `KEYCLOAK_VERSION == 3` uses the AuthZ `entitlement` endpoint; otherwise the UMA ticket endpoint (`services/oidc_profile.py:298-307`). Keep this branch when adding entitlement-related code.

### Tests

Tests live under `src/django_keycloak/tests/`, mirroring the production package layout (one directory per module-under-test). `pytest.ini` points at `django_keycloak.tests.settings`, which configures sqlite-in-memory and an MD5 hasher. `factories.py` (factory_boy) is the canonical way to build `Server` / `Realm` / `Client` / `OpenIdConnectProfile` fixtures.

The Keycloak HTTP client is always mocked in unit tests — there is no integration test harness in this package. For end-to-end verification, use the `example/` Docker Compose stack.

## Conventions

- This fork targets **Django 5** (branch `django-5`). Recent commits removed Django <4 idioms: `default_app_config` is gone (app config is auto-discovered from `apps.KeycloakAppConfig`), and `django.conf.urls.url` was replaced with `django.urls.re_path`. Note that `setup.py` / `.travis.yml` still declare `Django>=1.11` and Python 2.7 — those metadata files are stale and do **not** reflect the supported runtime. Write modern Python 3 / Django 5 code.
- Imports of sibling `services` submodules inside model methods are intentionally deferred (`import django_keycloak.services.x` inside a method) to break circular imports — don't hoist them to module top.
- Lint config: flake8 with default settings against `src/` (no project-specific overrides).
