"""Local private-pilot configuration and preflight checks."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path

from .core import ReviewError, now
from .service import validate_slug


REQUIRED_ENV = (
    "YANXU_DB_PASSWORD",
    "YANXU_BOOTSTRAP_ORG_SLUG",
    "YANXU_BOOTSTRAP_ORG_NAME",
    "YANXU_BOOTSTRAP_TOKEN",
    "YANXU_GITHUB_WEBHOOK_SECRET",
    "YANXU_SECURE_COOKIES",
    "YANXU_PORT",
)


def _compose_dir(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir() or not (resolved / "compose.yaml").is_file():
        raise ReviewError("Compose directory must contain compose.yaml")
    return resolved


def _organization_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise ReviewError("Organization name must be between 1 and 120 characters")
    value = value.strip()
    if any(character in value for character in "\r\n'"):
        raise ReviewError("Organization name cannot contain line breaks or single quotes")
    return value


def initialize_pilot(compose_dir: Path, organization_slug: str, organization_name: str,
                     port: int = 8080) -> dict:
    compose_dir = _compose_dir(compose_dir)
    slug = validate_slug(organization_slug)
    name = _organization_name(organization_name)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ReviewError("Port must be between 1 and 65535")
    env_path = compose_dir / ".env"
    if env_path.exists():
        raise ReviewError(".env already exists; pilot initialization will not overwrite it")
    values = {
        "YANXU_DB_PASSWORD": secrets.token_urlsafe(32),
        "YANXU_BOOTSTRAP_ORG_SLUG": slug,
        "YANXU_BOOTSTRAP_ORG_NAME": f"'{name}'",
        "YANXU_BOOTSTRAP_TOKEN": secrets.token_urlsafe(32),
        "YANXU_GITHUB_WEBHOOK_SECRET": secrets.token_urlsafe(32),
        "YANXU_SECURE_COOKIES": "false",
        "YANXU_PORT": str(port),
    }
    temporary = compose_dir / f".env.{secrets.token_hex(8)}.tmp"
    try:
        temporary.write_text("".join(f"{key}={values[key]}\n" for key in REQUIRED_ENV), encoding="utf-8")
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(env_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "status": "INITIALIZED",
        "environment_file": str(env_path),
        "organization_slug": slug,
        "port": port,
        "secrets_printed": False,
        "next_commands": ["python -m yanxu pilot doctor --compose-dir .",
                          "docker compose up -d --build"],
    }


def _read_env(path: Path) -> dict[str, str]:
    values = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReviewError(".env cannot be read") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ReviewError(".env contains a line without KEY=VALUE")
        key, value = stripped.split("=", 1)
        if key in values:
            raise ReviewError(f".env contains duplicate key: {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def _command_check(arguments: list[str], compose_dir: Path, runner) -> bool:
    try:
        result = runner(arguments, cwd=str(compose_dir), capture_output=True, text=True, check=False)
    except OSError:
        return False
    return result.returncode == 0


def inspect_pilot(compose_dir: Path, output: Path, runner=subprocess.run) -> dict:
    compose_dir = _compose_dir(compose_dir)
    checks = []

    def check(identifier: str, passed: bool, message: str):
        checks.append({"id": identifier, "status": "PASS" if passed else "FAIL", "message": message})

    env_path = compose_dir / ".env"
    check("compose_file", True, "compose.yaml is present")
    if not env_path.is_file():
        check("environment_file", False, ".env is missing; run pilot init")
        values = {}
    else:
        check("environment_file", True, ".env is present")
        try:
            values = _read_env(env_path)
        except ReviewError as exc:
            values = {}
            check("environment_format", False, str(exc))
    missing = [key for key in REQUIRED_ENV if not values.get(key)]
    check("required_values", not missing,
          "all required values are present" if not missing else "missing required environment keys")
    placeholders = any("replace-with" in value.lower() for value in values.values())
    check("placeholder_values", not placeholders,
          "placeholder values are absent" if not placeholders else "replace example placeholder values")
    secrets_valid = all(len(values.get(key, "")) >= 24 for key in (
        "YANXU_DB_PASSWORD", "YANXU_BOOTSTRAP_TOKEN", "YANXU_GITHUB_WEBHOOK_SECRET"))
    check("secret_lengths", secrets_valid,
          "private values meet the minimum length" if secrets_valid else "private values must be at least 24 characters")
    try:
        validate_slug(values.get("YANXU_BOOTSTRAP_ORG_SLUG", ""))
        slug_valid = True
    except ReviewError:
        slug_valid = False
    check("organization_slug", slug_valid,
          "organization slug is valid" if slug_valid else "organization slug is invalid")
    try:
        port_valid = 1 <= int(values.get("YANXU_PORT", "")) <= 65535
    except ValueError:
        port_valid = False
    check("port", port_valid, "port is valid" if port_valid else "port must be between 1 and 65535")
    cookie_valid = values.get("YANXU_SECURE_COOKIES", "").lower() in {"true", "false"}
    check("secure_cookies", cookie_valid,
          "secure cookie mode is explicit" if cookie_valid else "YANXU_SECURE_COOKIES must be true or false")
    docker_ok = _command_check(["docker", "version"], compose_dir, runner)
    check("docker", docker_ok, "Docker is available" if docker_ok else "Docker is unavailable")
    compose_ok = docker_ok and _command_check(["docker", "compose", "version"], compose_dir, runner)
    check("docker_compose", compose_ok,
          "Docker Compose is available" if compose_ok else "Docker Compose is unavailable")
    config_ok = compose_ok and not missing and not placeholders and _command_check(
        ["docker", "compose", "config", "--quiet"], compose_dir, runner)
    check("compose_config", config_ok,
          "Docker Compose configuration is valid" if config_ok else "Docker Compose configuration is not ready")
    result = {
        "schema_version": 1,
        "kind": "yanxu.pilot_preflight",
        "created_at": now(),
        "status": "READY" if all(item["status"] == "PASS" for item in checks) else "NEEDS_WORK",
        "checks": checks,
        "secrets_in_output": False,
    }
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": result["status"], "report": str(output), "checks": checks,
            "secrets_in_output": False}
