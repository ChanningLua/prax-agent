# Prax v0.2.0

_Released 2026-04-22_

This release adds OAuth login and invoice PDF export while tightening core reliability. It also introduces an API response envelope change that requires client updates.

## Highlights

- Added OAuth login support for Google, GitHub, and Microsoft.
- Added invoice PDF export in the billing portal.
- Changed API responses to return resources inside a `data` envelope.

## What's Changed

### Breaking
- **api**: wrapped responses in a data envelope. Refs #31.

### Added
- billing: added invoice PDF export. Refs #18.
- auth: added OAuth login support. Refs #12.

### Changed
- core: cached config parse result.
- core: extracted shared time helper.

### Fixed
- billing: fixed duplicate charges on retry. Refs #23.
- auth: fixed token refresh race. Refs #17.

## Upgrading

Update API consumers to unwrap response payloads from the `data` field. No other migration is needed.

## Credits

Prax Demo

---

Full diff: https://github.com/ChanningLua/prax-agent/compare/v0.1.0...v0.2.0
