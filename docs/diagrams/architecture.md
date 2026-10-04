# Architecture

[Back to README](../../README.md)

MK-Collector separates RouterOS access, live state, short-term persistence, the local API, and presentation. Only the collector workers communicate with the router.

```mermaid
flowchart TB
    subgraph Router["MikroTik RouterOS v7"]
        REST["REST API<br/>monitor operations only"]
    end

    subgraph Collector["MK-Collector · Python / Flask"]
        TW["Traffic worker<br/>default: every 1 s"]
        DW["DDM worker<br/>default: every 5 s"]
        LIVE["Thread-safe live state<br/>60 traffic samples + latest DDM"]
        DB[("SQLite<br/>traffic_samples + ddm_samples")]
        API["Flask API<br/>/api/state · /api/history"]
    end

    UI["Browser dashboard<br/>HTML · CSS · JavaScript · Chart.js"]

    REST -->|"interface/monitor-traffic"| TW
    REST -->|"interface/ethernet/monitor"| DW
    TW -->|"valid samples"| LIVE
    TW -->|"valid samples"| DB
    DW -->|"latest optical state"| LIVE
    DW -->|"valid DDM samples"| DB
    LIVE -->|"live snapshot"| API
    DB -->|"historical traffic query"| API
    API -->|"JSON over localhost"| UI
```

## Boundaries

- RouterOS access is limited in code to two monitor endpoints.
- Failed traffic requests create live gaps and do not write artificial zeroes to SQLite.
- SQLite uses one connection per operation instead of sharing a connection between workers.
- The browser never communicates with RouterOS and never receives RouterOS credentials.
- Historical API queries currently return traffic history; persisted DDM samples are not exposed as a historical endpoint.
