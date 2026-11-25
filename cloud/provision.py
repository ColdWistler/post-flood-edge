"""Provision the ThingsBoard artifacts with the REST API.

Dry-run is the default and performs no network calls. ``--apply`` authenticates
with ``TB_JWT``/``TB_API_KEY`` (or logs in with ``TB_ADMIN_USER``/
``TB_ADMIN_PASSWORD``), fills the token placeholders in ``devices.json`` from the
environment, and creates the device profile, three devices, dashboard and WQI rule
chain. Secrets are never written to disk or printed.

    python -m cloud.provision --dry-run
    python -m cloud.provision --apply --jwt "$TB_JWT"
    python -m cloud.provision --apply --api-key "$TB_API_KEY"
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

from .benchmark import ThingsBoardClient

DASHBOARD_CANDIDATES = (
    "dashboard_thingsboard_cloud.json",
    "dashboard.json",
    "dashboard_minimal.json",
)

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
TOKEN_PATTERN = re.compile(r"\$\{(TB_TOKEN_[A-Z0-9_]+)\}")
NODE_TOKENS = {
    "TB_TOKEN_NODE_A": "node-a",
    "TB_TOKEN_NODE_B": "node-b",
    "TB_TOKEN_NODE_C": "node-c",
}


def load_json(name: str) -> Any:
    """Load one committed artifact."""
    path = ARTIFACTS / name
    if not path.exists():
        raise FileNotFoundError(f"missing artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


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
    request = urllib.request.Request(
        base.rstrip("/") + "/api/auth/login",
        data=json.dumps({"username": user, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    import ssl

    context = ssl.create_default_context(cafile=ca_cert or None)
    with urllib.request.urlopen(request, timeout=20, context=context) as response:
        data = json.loads(response.read().decode("utf-8"))
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise ValueError("admin login response contains no token")
    return str(token)


class Provisioner:
    """Create artifacts idempotently enough for a repeatable demo setup."""

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
        query = urllib.parse.urlencode({"deviceProfileName": name})
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
                "GET", "/api/tenant/devices?" + urllib.parse.urlencode({"deviceName": name})
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
        existing = self.client.request("GET", "/api/ruleChains")
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
    transport.add_argument("--apply", action="store_true", help="call ThingsBoard REST")
    transport.add_argument("--dry-run", action="store_true", help="validate only (default)")
    parser.add_argument(
        "--host",
        default=os.environ.get("TB_HOST", os.environ.get("TB_BASE_URL", "")),
    )
    parser.add_argument("--jwt", default=os.environ.get("TB_JWT", ""))
    parser.add_argument("--api-key", default=os.environ.get("TB_API_KEY", ""))
    parser.add_argument("--ca-cert", default=os.environ.get("TB_CA_CERT", ""))
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
        default=10800000,
        help="device inactivity timeout in ms (180 min normal; use 2700000 for 45 min burst)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Load, validate and optionally provision the Cloud Track artifacts."""
    args = build_parser().parse_args(argv)
    try:
        dashboard_name = args.dashboard or select_dashboard()
        profile = load_json("device_profile.json")
        devices = load_json("devices.json")
        dashboard = load_json(dashboard_name)
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


if __name__ == "__main__":
    raise SystemExit(main())
