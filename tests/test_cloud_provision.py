"""Safety checks for tenant provisioning before it is run with real credentials."""

import pytest

from cloud.provision import ensure_device, ensure_profile, redact_sensitive


class FakeTenant:
    def __init__(self):
        self.calls = []
        self.profiles = [{"id": {"id": "default-id"}, "name": "default", "default": True}]
        self.devices = []

    def pages(self, path):
        if path == "/api/deviceProfiles":
            return iter(self.profiles)
        if path == "/api/tenant/devices":
            return iter(self.devices)
        raise AssertionError(path)

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/api/deviceProfile/default-id"):
            return {
                "id": {"id": "default-id"}, "tenantId": {"id": "tenant-id"},
                "name": "default", "default": True,
                "profileData": {"transportConfiguration": {"type": "DEFAULT"}},
            }
        if (method, path) == ("POST", "/api/deviceProfile"):
            assert "id" not in body and "tenantId" not in body and "default" not in body
            return {"id": {"id": "new-profile"}, **body}
        if (method, path) == ("POST", "/api/device"):
            return {"id": {"id": "new-device"}, **body}
        raise AssertionError((method, path))


def test_profile_clone_does_not_overwrite_default():
    tenant = FakeTenant()
    created = ensure_profile(tenant)
    assert created["id"]["id"] == "new-profile"
    assert created["profileData"]["transportConfiguration"]["type"] == "DEFAULT"
    assert created["profileData"]["alarms"] == []


def test_existing_device_under_other_profile_is_not_changed():
    tenant = FakeTenant()
    tenant.devices = [{
        "id": {"id": "existing"}, "name": "node-a",
        "deviceProfileId": {"id": "someone-elses-profile"},
    }]
    with pytest.raises(RuntimeError, match="another profile"):
        ensure_device(tenant, "node-a", "new-profile")
    assert not any(method == "POST" for method, _, _ in tenant.calls)


def test_snapshot_redacts_nested_credentials():
    snapshot = redact_sensitive({
        "name": "Post-Flood Water Source",
        "profileData": {"provisionConfiguration": {
            "provisionDeviceKey": "key-value", "provisionDeviceSecret": "secret-value",
        }},
    })
    assert "key-value" not in str(snapshot)
    assert "secret-value" not in str(snapshot)
    assert snapshot["name"] == "Post-Flood Water Source"
