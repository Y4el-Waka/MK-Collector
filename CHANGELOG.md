# Changelog

All notable changes to MK-Collector are documented in this file.

This project follows a simple versioned changelog. Dates are intentionally omitted where the repository history does not provide a verifiable release date.

## [Unreleased]

### Changed

- Made physical RouterOS customer and uplink interface names configurable while preserving logical `ether1` and `sfp-sfpplus1` API, SQLite, statistics, and dashboard contracts.

### Documentation

- Added bilingual English and Spanish project documentation.
- Added Mermaid architecture, deployment topology, data-flow, and component diagrams.
- Added security, validation, limitations, roadmap, and screenshot guidance for public repository presentation.
- Added the project MIT license.

## [0.2.0]

### Added

- Short-term SQLite persistence for traffic and DDM samples.
- LIVE 60s, 20-minute, 1-hour, and 2-hour dashboard windows.
- Raw-window current, minimum, maximum, average, and peak timestamp statistics.
- Peak-preserving downsampling for historical chart responses.
- SFP/DDM visibility for link state, negotiated rate, duplex, temperature, voltage, TX bias, optical power, and available module identity.
- Responsive Chart.js dashboard with current service summary and per-series visibility controls.
- Automatic retention cleanup and recovery after temporary RouterOS failures.
- Automated coverage for normalization, read-only endpoint enforcement, recovery, SQLite, statistics, and API windows.

### Security

- Limited RouterOS access in code to `interface/monitor-traffic` and `interface/ethernet/monitor`.
- Kept RouterOS credentials server-side through `.env` configuration.
- Preserved missing samples as gaps instead of artificial zero traffic.
