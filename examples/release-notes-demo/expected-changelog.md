# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-04-22

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

### Documentation
- auth: explained the MFA flow.
- setup: added setup guide.


## [0.1.0] - 2026-01-01

### Added
- Initial scaffold.