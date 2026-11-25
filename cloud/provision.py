"""Provision the ThingsBoard artifacts and the repeatable device inventory.

Dry-run is the default and performs no network calls. ``--apply`` authenticates
with ``TB_JWT``/``TB_API_KEY`` (or logs in with ``TB_ADMIN_USER``/
``TB_ADMIN_PASSWORD``), fills the token placeholders in ``devices.json`` from the
environment, and creates the device profile, three devices, dashboard and WQI rule
chain. Secrets are never written to disk or printed.

A separate ``--device-setup`` path preserves the version-compatible device
provisioning used by the dashboard workstream. It reuses the tenant's default
profile schema, creates the three nodes with coordinates and the normal
inactivity attribute, saves a redacted profile snapshot, and writes device
tokens only to the ignored local ``.env``. It accepts an API key from the
ignored ``.thingsboard-auth`` file or ``TB_AUTH``; never pass secrets as CLI
arguments.

The dashboard imported by ``--apply`` is the eight-widget triage dashboard
(``dashboard_triage.json``), built from the verified ThingsBoard Cloud export.
If that file is absent, the verified six-chart export
(``dashboard_thingsboard_cloud.json``) is used; the older alias-based
``dashboard.json`` remains only as a CE 3.x fallback.

    python -m cloud.provision --dry-run
    python -m cloud.provision --apply              # TB_HOST + TB_JWT/TB_API_KEY
    python -m cloud.provision --apply --jwt "$TB_JWT"
    python -m cloud.provision --apply --api-key "$TB_API_KEY"
    python -m cloud.provision --device-setup       # TB_URL + TB_AUTH
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .benchmark import ThingsBoardClient

DASHBOARD_CANDIDATES = (
    "dashboard_thingsboard_cloud.json",
    "dashboard.json",
    "dashboard_minimal.json",
)

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
DASHBOARD_ARTIFACT = "dashboard_triage.json"
CLOUD_DASHBOARD_ARTIFACT = "dashboard_thingsboard_cloud.json"
LEGACY_DASHBOARD_ARTIFACT = "dashboard.json"
TOKEN_PATTERN = re.compile(r"\$\{(TB_TOKEN_[A-Z0-9_]+)\}")
NODE_TOKENS = {
    "TB_TOKEN_NODE_A": "node-a",
    "TB_TOKEN_NODE_B": "node-b",
    "TB_TOKEN_NODE_C": "node-c",
}

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
                word in key.lower()
                for word in ("secret", "password", "token", "provisiondevicekey")
            ) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def load_json(name: str) -> Any:
    """Load one committed artifact."""
    path = ARTIFACTS / name
    if not path.exists():
        raise FileNotFoundError(f"missing artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_dashboard() -> tuple[dict[str, Any], str]:
    """Load the eight-widget triage dashboard with verified fallbacks.

    The alias-based ``dashboard.json`` was rejected by ThingsBoard Cloud on UI
    import. ``dashboard_triage.json`` is derived from the verified Cloud export
    and adds the native gauges, map, alarms table and entity table required by
    the rubric; the six-chart export is retained as a safe fallback.
    """
    for name in (DASHBOARD_ARTIFACT, CLOUD_DASHBOARD_ARTIFACT, LEGACY_DASHBOARD_ARTIFACT):
        if (ARTIFACTS / name).exists():
            return load_json(name), name
    raise FileNotFoundError(
        f"missing dashboard artifact: {ARTIFACTS / DASHBOARD_ARTIFACT}"
    )


def fill_tokens(body: Any, environ: dict[str, str] | None = None) -> Any:
    """Replace ``${TB_TOKEN_*}`` placeholders, refusing empty credentials."""
    env = os.environ if environ is None else environ
    missing = [
        name
        for name in NODE_TOKENS
        if name not in env or not str(env[name]).strip()
    ]
    if missing:
        raise ValueError("missing access tokens: " + ", ".join(sorted(missing)))

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            def substitute(match: re.Match[str]) -> str:
                name = match.group(1)
                if name not in env or not str(env[name]).strip():
                    raise ValueError(f"empty access token: {name}")
                return str(env[name])

            return TOKEN_PATTERN.sub(substitute, value)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    return replace(body)


def admin_login(host: str, user: str, password: str, ca_cert: str | None = None) -> str:
    """Obtain a JWT from the public admin login endpoint."""
    base = host if "://" in host else "https://" + host
    request = Request(
        base.rstrip("/") + "/api/auth/login",
        data=json.dumps({"username": user, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    import ssl

    context = ssl.create_default_context(cafile=ca_cert or None)
    with urlopen(request, timeout=20, context=context) as response:
        data = json.loads(response.read().decode("utf-8"))
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise ValueError("admin login response contains no token")
    return str(token)


class ThingsBoard:
    """Version-compatible REST client for the dashboard workstream."""

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
            raise RuntimeError(
                f"ThingsBoard {method} {path}: HTTP {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"Cannot reach ThingsBoard at {self.url}: {exc.reason}"
            ) from exc
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
    """Create or reuse the version-compatible water-source device profile."""
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
    """Create one device without changing a device already under another profile."""
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
    """Write device tokens only to the ignored local .env file."""
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
    """Provision the profile and three devices using the tenant's own schema."""
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
    snapshot.write_text(
        json.dumps(redact_sensitive(actual_profile), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved redacted profile API snapshot: {snapshot.relative_to(REPO_ROOT)}")
    host = urlparse(tb.url).hostname
    assert host is not None
    update_local_env(tokens, host)
    print(f"Burst-mode target: set per-device inactivityTimeout={BURST_TIMEOUT_MS} ms")
    print("Next: run the edge MQTT simulator, build dashboard, export it in ThingsBoard UI")


class Provisioner:
    """Create artifact resources idempotently enough for a repeatable demo setup."""

    def __init__(self, client: ThingsBoardClient) -> None:
        self.client = client
        self.created: list[str] = []
        self.skipped: list[str] = []

    def _create(self, label: str, path: str, body: Any, allow_exists: bool = True) -> Any:
        try:
            result = self.client.request("POST", path, body)
        except urllib.error.HTTPError as exc:
            if allow_exists and exc.code in (400, 409):
                self.skipped.append(f"{label} (already exists or rejected: {exc.code})")
                return None
            raise
        self.created.append(label)
        return result

    def device_profile(self, profile: dict[str, Any]) -> str | None:
        """Create the water-quality device profile with its alarm rules."""
        name = profile.get("name", "water-quality-node")
        query = urllib.parse.urlencode(
            {"deviceProfileName": name, "pageSize": 100, "page": 0}
        )
        existing = self.client.request("GET", "/api/deviceProfiles?" + query)
        if isinstance(existing, dict) and existing.get("data"):
            self.skipped.append(f"device profile {name} (exists)")
            found = existing["data"][0].get("id", {})
            return found.get("id") if isinstance(found, dict) else None
        saved = self._create(f"device profile {name}", "/api/deviceProfile", profile)
        identifier = saved.get("id", {}).get("id") if isinstance(saved, dict) else None
        return identifier

    def devices(
        self,
        devices: dict[str, Any],
        profile_id: str | None,
        inactivity_ms: int,
    ) -> None:
        """Create one device per node with its token, profile and inactivity timeout."""
        for device in devices.get("devices", []):
            name = device.get("name", "")
            existing = self.client.request(
                "GET",
                "/api/tenant/devices?"
                + urllib.parse.urlencode({"deviceName": name, "pageSize": 100, "page": 0}),
            )
            if isinstance(existing, dict) and existing.get("id"):
                self.skipped.append(f"device {name} (exists)")
                continue
            token = device.get("accessToken")
            body = {key: value for key, value in device.items() if key != "accessToken"}
            if profile_id:
                body["deviceProfileId"] = {"entityType": "DEVICE_PROFILE", "id": profile_id}
            created = self._create(f"device {name}", "/api/device", body, allow_exists=False)
            identifier = created.get("id", {}).get("id") if isinstance(created, dict) else None
            if not identifier or not token:
                raise ValueError(f"cannot set credentials for {name}")
            self.client.request(
                "POST",
                f"/api/device/{identifier}/credentials",
                {"credentialsType": "ACCESS_TOKEN", "credentialsId": token},
            )

            # Inactivity timeout is a SERVER_SCOPE device attribute in ThingsBoard v3,
            # not a device-profile field. Milliseconds: 180 min normal, 45 min burst.
            if identifier and inactivity_ms > 0:
                self.client.request(
                    "POST",
                    f"/api/plugins/telemetry/DEVICE/{identifier}/attributes/SERVER_SCOPE",
                    {"inactivityTimeout": int(inactivity_ms)},
                )

    def dashboard(self, dashboard: dict[str, Any]) -> None:
        """Import the current-style dashboard export with a plain POST."""
        bare = dashboard.get("dashboard", dashboard)
        self._create("operational dashboard", "/api/dashboard", bare)

    def rule_chain(self, chain: dict[str, Any], set_root: bool) -> None:
        """Save the chain header and metadata, then optionally set it as root.

        The exported envelope is importable directly in the UI. The REST API
        splits that import into two calls so the returned UUID is known.
        """
        header = dict(chain.get("ruleChain", {}))
        metadata = dict(chain.get("metadata", {}))
        name = header.get("name", "WQI rule chain")
        existing = self.client.request(
            "GET",
            "/api/ruleChains?" + urllib.parse.urlencode({"pageSize": 100, "page": 0}),
        )
        if isinstance(existing, dict) and any(
            item.get("name") == name for item in existing.get("data", [])
        ):
            self.skipped.append(f"rule chain {name} (exists)")
            return
        saved = self.client.request("POST", "/api/ruleChain", header)
        identifier = saved.get("id", {}).get("id") if isinstance(saved, dict) else None
        if not identifier:
            raise ValueError("rule-chain save returned no id")
        metadata["ruleChainId"] = {"entityType": "RULE_CHAIN", "id": identifier}
        self.client.request("POST", "/api/ruleChain/metadata", metadata)
        self.created.append(f"rule chain {name}")
        if not set_root:
            return
        try:
            self.client.request("POST", f"/api/ruleChain/{identifier}/root")
            self.created.append("WQI rule chain set as root")
        except urllib.error.HTTPError as exc:
            print(
                f"warning: could not set root rule chain ({exc.code}); "
                "activate it from the rule-chains UI",
                file=sys.stderr,
            )


def select_dashboard() -> str:
    """Pick the best available dashboard artifact, preferring Cloud-verified JSON."""
    for name in DASHBOARD_CANDIDATES:
        if (ARTIFACTS / name).exists():
            return name
    raise FileNotFoundError(
        "missing dashboard artifact; expected one of: " + ", ".join(DASHBOARD_CANDIDATES)
    )


def build_parser() -> argparse.ArgumentParser:
    """Define the provisioning CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--dry-run", action="store_true", help="validate only (default)")
    transport.add_argument(
        "--apply",
        action="store_true",
        help="import the committed artifacts over REST using TB_HOST and TB_JWT",
    )
    transport.add_argument(
        "--device-setup",
        action="store_true",
        help="create/reuse the profile and devices using TB_URL and TB_AUTH",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("TB_HOST", os.environ.get("TB_BASE_URL", "")),
    )
    parser.add_argument("--jwt", default=os.environ.get("TB_JWT", ""))
    parser.add_argument("--api-key", default=os.environ.get("TB_API_KEY", ""))
    parser.add_argument("--ca-cert", default=os.environ.get("TB_CA_CERT", ""))
    parser.add_argument("--tb-url", default=os.environ.get("TB_URL", DEFAULT_URL))
    parser.add_argument(
        "--dashboard",
        default="",
        help="dashboard artifact filename in cloud/artifacts (default: auto-select)",
    )
    parser.add_argument(
        "--set-root",
        action="store_true",
        help="make the imported WQI chain the root chain (overwrites the current root)",
    )
    parser.add_argument(
        "--inactivity-ms",
        type=int,
        default=NORMAL_TIMEOUT_MS,
        help="device inactivity timeout in ms (180 min normal; use 2700000 for 45 min burst)",
    )
    return parser


def _apply_artifacts(args: argparse.Namespace) -> int:
    """Validate and optionally import the four committed artifact files."""
    try:
        profile = load_json("device_profile.json")
        devices = load_json("devices.json")
        if args.dashboard:
            dashboard = load_json(args.dashboard)
            dashboard_name = args.dashboard
        else:
            dashboard, dashboard_name = load_dashboard()
        chain = load_json("wqi_rule_chain.json")
        if args.apply:
            devices = fill_tokens(devices)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"provision: {exc}", file=sys.stderr)
        return 2

    if not args.apply:
        for name in ("device_profile.json", "devices.json", dashboard_name, "wqi_rule_chain.json"):
            print(f"validated {name}")
        print("dry-run: no network calls; tokens remain placeholders")
        return 0

    if not args.host:
        print("provision: --apply needs TB_HOST/TB_BASE_URL", file=sys.stderr)
        return 2
    api_key = args.api_key
    jwt = args.jwt
    if not api_key and not jwt:
        user = os.environ.get("TB_ADMIN_USER", "")
        password = os.environ.get("TB_ADMIN_PASSWORD", "")
        if not user or not password:
            print(
                "provision: --apply needs TB_API_KEY, TB_JWT, or TB_ADMIN_USER/TB_ADMIN_PASSWORD",
                file=sys.stderr,
            )
            return 2
        try:
            jwt = admin_login(args.host, user, password, args.ca_cert or None)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"provision: login failed: {exc}", file=sys.stderr)
            return 2

    provisioner = Provisioner(
        ThingsBoardClient(args.host, jwt=jwt, api_key=api_key, ca_cert=args.ca_cert or None)
    )
    try:
        profile_id = provisioner.device_profile(profile)
        provisioner.devices(devices, profile_id, args.inactivity_ms)
        provisioner.dashboard(dashboard)
        provisioner.rule_chain(chain, args.set_root)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"provision: request failed: {exc}", file=sys.stderr)
        return 1

    for item in provisioner.created:
        print(f"created: {item}")
    for item in provisioner.skipped:
        print(f"skipped: {item}")
    return 0


def _device_setup(args: argparse.Namespace) -> int:
    """Provision the profile and devices with an API key, never a CLI secret."""
    auth = os.environ.get("TB_AUTH", "")
    if not auth:
        key_file = REPO_ROOT / ".thingsboard-auth"
        if key_file.exists():
            auth = key_file.read_text(encoding="utf-8").strip()
            if auth and not auth.startswith(("ApiKey ", "Bearer ")):
                auth = "ApiKey " + auth
    if not auth:
        print(
            "provision: --device-setup needs TB_AUTH or an ignored .thingsboard-auth file",
            file=sys.stderr,
        )
        return 2
    try:
        apply(ThingsBoard(args.tb_url, auth))
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"provision: device setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Load, validate and optionally provision the Cloud Track resources."""
    args = build_parser().parse_args(argv)
    if args.device_setup:
        return _device_setup(args)
    return _apply_artifacts(args)


if __name__ == "__main__":
    raise SystemExit(main())
