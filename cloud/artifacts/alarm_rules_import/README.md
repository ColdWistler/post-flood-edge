# Alarm rule import files

These nine files are the importable form of `cloud/artifacts/alarm_rules.json` for
ThingsBoard Cloud tenants whose **Alarm rules** page has an **Import** action.

- Import files **one at a time**, not as a single bundle.
- Each file is one `CalculatedField` of type `ALARM`.
- `cloud/artifacts/alarm_rules.json` is a specification and cannot be imported.
- `cloud/artifacts/device_profile.json` uses the older `profileData.alarms`
  representation. Recent Cloud versions may import the profile but hide that
  deprecated list, leaving the profile's Alarm Rules tab empty. In that case use
  these files and choose the `post-flood-water-quality` device profile when the
  import dialog asks for the target entity.

Regenerate the files after changing the specification or generator:

```bash
python tools/build_alarm_rule_imports.py
```

See `docs/TENANT_CHECKLIST.md` for the exact UI sequence and evidence to retain.
