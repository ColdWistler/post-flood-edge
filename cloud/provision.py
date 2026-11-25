"""Provision the three ThingsBoard devices for Anushree's cloud track.

No external packages are needed. The script is idempotent and uses the tenant's
existing default device profile as the version-compatible template. It never
prints access tokens or the administrator credential.

Preview: python -m cloud.provision
Apply:   python -m cloud.provision --apply

The user's tenant defaults to https://eu.thingsboard.cloud. Put only the API key
in the ignored .thingsboard-auth file in the repository root. Alternatively set
TB_AUTH='ApiKey ...' in the environment. Never pass secrets as CLI arguments.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


PROFILE_NAME = "Post-Flood Water Source"
NODES = {
    "node-a": (27.7172, 85.3240),
    "node-b": (27.6790, 85.3190),
    "node-c": (27.7500, 85.3700),
}
NORMAL_TIMEOUT_MS = 180 * 60 * 1000
BURST_TIMEOUT_MS = 45 * 60 * 1000
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://eu.thingsboard.cloud"


def entity_id(value: dict) -> str:
    return value["id"]["id"]


def redact_sensitive(value):
    """Keep the checked-in API snapshot free of credential-like fields."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(
                word in key.lower() for word in ("secret", "password", "token", "provisiondevicekey")
            ) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


class ThingsBoard:
    def __init__(self, url: str, auth: str):
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("TB_URL must be a full http(s) URL")
        if not auth.startswith(("ApiKey ", "Bearer ")):
            raise ValueError("TB_AUTH must start with 'ApiKey ' or 'Bearer '")
        self.url = url.rstrip("/")
        self.auth = auth

    def request(self, method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(
            self.url + path,
            data=data,
            method=method,
            headers={
                "X-Authorization": self.auth,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=20) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"ThingsBoard {method} {path}: HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Cannot reach ThingsBoard at {self.url}: {exc.reason}") from exc
        return json.loads(raw) if raw else None

    def pages(self, path: str):
        page = 0
        while True:
            suffix = urlencode({"pageSize": 100, "page": page})
            result = self.request("GET", f"{path}?{suffix}")
            yield from result.get("data", [])
            if not result.get("hasNext", False):
                break
            page += 1


def ensure_profile(tb: ThingsBoard) -> dict:
    profiles = list(tb.pages("/api/deviceProfiles"))
    existing = next((p for p in profiles if p.get("name") == PROFILE_NAME), None)
    if existing:
        print(f"Profile exists: {PROFILE_NAME}")
        return tb.request("GET", f"/api/deviceProfile/{entity_id(existing)}")

    default = next((p for p in profiles if p.get("default") or p.get("isDefault")), None)
    if default is None:
        default = next((p for p in profiles if p.get("name", "").lower() == "default"), None)
    if default is None:
        raise RuntimeError("No default profile found. Create one in the tenant UI first.")

    source = tb.request("GET", f"/api/deviceProfile/{entity_id(default)}")
    # Copy the tenant's own schema, so this also works with older CE versions.
    profile = {k: v for k, v in source.items() if k not in {
        "id", "createdTime", "tenantId", "externalId", "version",
        "default", "isDefault", "devicesCount",
    }}
    profile["name"] = PROFILE_NAME
    profile["description"] = (
        "Three virtual post-flood water-source nodes; MQTT/TLS telemetry. "
        "Alarm rules are maintained by Sambid's cloud workstream."
    )
    # The shared default may contain unrelated alarms; Sambid owns this profile's rules.
    if isinstance(profile.get("profileData"), dict):
        profile["profileData"]["alarms"] = []
    created = tb.request("POST", "/api/deviceProfile", profile)
    print(f"Created profile: {PROFILE_NAME}")
    return created


def ensure_device(tb: ThingsBoard, name: str, profile_id: str) -> dict:
    candidates = tb.pages("/api/tenant/devices")
    existing = next((d for d in candidates if d.get("name") == name), None)
    if existing:
        assigned = existing.get("deviceProfileId", {}).get("id")
        if assigned != profile_id:
            raise RuntimeError(
                f"{name} already exists under another profile ({assigned}); "
                "review it in the UI before changing anything."
            )
        print(f"Device exists: {name}")
        return existing
    body = {
        "name": name,
        "label": f"Water source {name[-1].upper()}",
        "deviceProfileId": {"entityType": "DEVICE_PROFILE", "id": profile_id},
    }
    created = tb.request("POST", "/api/device", body)
    print(f"Created device: {name}")
    return created


def update_local_env(tokens: dict[str, str], host: str) -> None:
    path = REPO_ROOT / ".env"
    if path.exists():
        old = path.read_text(encoding="utf-8")
    else:
        old = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    replacements = {"TB_HOST": host}
    replacements.update({
        "TB_TOKEN_" + node.upper().replace("-", "_"): token
        for node, token in tokens.items()
    })
    lines = []
    seen = set()
    for line in old.splitlines():
        key = line.split("=", 1)[0]
        if key in replacements and not line.startswith("#"):
            lines.append(f"{key}={replacements[key]}")
            seen.add(key)
        else:
            lines.append(line)
    lines.extend(f"{key}={value}" for key, value in replacements.items() if key not in seen)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    print("Updated ignored local .env with host and three device tokens")


def apply(tb: ThingsBoard) -> None:
    profile = ensure_profile(tb)
    pid = entity_id(profile)
    tokens = {}
    for name, (latitude, longitude) in NODES.items():
        device = ensure_device(tb, name, pid)
        did = entity_id(device)
        tb.request("POST", f"/api/plugins/telemetry/DEVICE/{did}/attributes/SERVER_SCOPE", {
            "latitude": latitude,
            "longitude": longitude,
            "inactivityTimeout": NORMAL_TIMEOUT_MS,
        })
        credentials = tb.request("GET", f"/api/device/{did}/credentials")
        if credentials.get("credentialsType") != "ACCESS_TOKEN":
            raise RuntimeError(f"{name} has non-token credentials; review in UI")
        tokens[name] = credentials["credentialsId"]
        print(f"Verified {name}: coordinates and normal inactivity {NORMAL_TIMEOUT_MS} ms")

    actual_profile = tb.request("GET", f"/api/deviceProfile/{pid}")
    export_dir = REPO_ROOT / "cloud" / "exports"
    export_dir.mkdir(exist_ok=True)
    snapshot = export_dir / "post-flood-device-profile-api.json"
    snapshot.write_text(json.dumps(redact_sensitive(actual_profile), indent=2) + "\n", encoding="utf-8")
    print(f"Saved redacted profile API snapshot: {snapshot.relative_to(REPO_ROOT)}")
    host = urlparse(tb.url).hostname
    assert host is not None
    update_local_env(tokens, host)
    print(f"Burst-mode target: set per-device inactivityTimeout={BURST_TIMEOUT_MS} ms")
    print("Next: run the edge MQTT simulator, build dashboard, export it in ThingsBoard UI")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="create/update tenant resources")
    args = parser.parse_args()
    if not args.apply:
        print("Plan: create/reuse profile, provision node-a/b/c, set coordinates and 180-min")
        print("inactivity attributes, save an API profile snapshot and ignored local .env.")
        print("Save an API key in ignored .thingsboard-auth, then run --apply.")
        print(f"Tenant URL: {DEFAULT_URL} (override with TB_URL if needed)")
        return
    url = os.environ.get("TB_URL", DEFAULT_URL)
    auth = os.environ.get("TB_AUTH", "")
    if not auth:
        key_file = REPO_ROOT / ".thingsboard-auth"
        if key_file.exists():
            auth = key_file.read_text(encoding="utf-8").strip()
            if auth and not auth.startswith(("ApiKey ", "Bearer ")):
                auth = "ApiKey " + auth
    if not auth:
        parser.error("--apply requires an ignored .thingsboard-auth file or TB_AUTH")
    apply(ThingsBoard(url, auth))


if __name__ == "__main__":
    main()
