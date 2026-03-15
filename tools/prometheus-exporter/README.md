# Enhanced Prometheus Exporter for RustChain

A comprehensive Prometheus metrics exporter that provides deep observability
into RustChain node operations, miner performance, and network health.

## Metrics Exposed

| Category | Metric | Type | Description |
|----------|--------|------|-------------|
| **Node Health** | `rustchain_node_up` | Gauge | Node reachability (1=up, 0=down) |
| | `rustchain_node_uptime_seconds` | Gauge | Reported node uptime |
| | `rustchain_node_db_status` | Gauge | Database read/write status |
| **Epoch** | `rustchain_current_epoch` | Gauge | Current epoch number |
| | `rustchain_current_slot` | Gauge | Current slot in epoch |
| | `rustchain_epoch_pot_rtc` | Gauge | Epoch reward pot |
| | `rustchain_epoch_progress_ratio` | Gauge | Fraction of epoch elapsed |
| | `rustchain_epoch_seconds_remaining` | Gauge | Estimated time until epoch rollover |
| **Per-Miner** | `rustchain_miner_hashrate` | Gauge | Hashrate per miner (labels: miner, hardware_type, arch) |
| | `rustchain_miner_uptime_seconds` | Gauge | Miner continuous uptime |
| | `rustchain_miner_last_attest_timestamp` | Gauge | Last attestation timestamp |
| | `rustchain_miner_antiquity_multiplier` | Gauge | Hardware antiquity multiplier |
| | `rustchain_active_miners_total` | Gauge | Miners active in last 30 min |
| | `rustchain_miners_by_hardware` | Gauge | Count bucketed by hardware type |
| | `rustchain_miners_by_arch` | Gauge | Count bucketed by architecture |
| **Transactions** | `rustchain_transactions_total` | Counter | Cumulative transactions observed |
| | `rustchain_tx_throughput_per_minute` | Gauge | Rolling 1-minute transaction rate |
| | `rustchain_tx_pending` | Gauge | Mempool depth |
| **System Resources** | `rustchain_node_cpu_percent` | Gauge | Node process CPU usage |
| | `rustchain_node_memory_rss_bytes` | Gauge | Node process resident memory |
| | `rustchain_node_memory_vms_bytes` | Gauge | Node process virtual memory |
| | `rustchain_node_open_file_descriptors` | Gauge | Open file descriptors |
| | `rustchain_system_cpu_percent` | Gauge | System-wide CPU usage |
| | `rustchain_system_memory_used_bytes` | Gauge | System memory in use |
| **Peers** | `rustchain_peer_count` | Gauge | Connected peer count |
| | `rustchain_peer_quality_score` | Gauge | Per-peer quality score (0-100) |
| | `rustchain_avg_peer_quality_score` | Gauge | Mean quality across peers |
| | `rustchain_peer_latency_ms` | Gauge | Per-peer latency |
| **Attestations** | `rustchain_attestation_success_total` | Counter | Successful attestations |
| | `rustchain_attestation_failure_total` | Counter | Failed attestations |
| | `rustchain_attestation_success_rate` | Gauge | Rolling success ratio (0-1) |
| **Fee Pool** | `rustchain_fee_pool_total_rtc` | Gauge | Total fees collected |
| | `rustchain_fee_pool_events_total` | Gauge | Fee event count |
| | `rustchain_fee_pool_growth_rate_rtc_per_min` | Gauge | Fee growth rate per minute |
| **Wallets** | `rustchain_wallet_balance_rtc` | Gauge | Current balance per address |
| | `rustchain_wallet_balance_delta_rtc` | Gauge | Balance change since last scrape |
| **API Latency** | `rustchain_api_request_duration_seconds` | Histogram | Request latency by endpoint |
| **Hall of Fame** | `rustchain_hall_of_fame_total_machines` | Gauge | Total machines in hall of fame |
| | `rustchain_hall_of_fame_highest_rust_score` | Gauge | Highest rust score |

## Quick Start

```bash
pip install prometheus-client requests psutil
python exporter.py
```

Metrics are served at `http://localhost:9200/metrics`.

## Configuration

All settings are controlled via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `RUSTCHAIN_NODE` | `https://rustchain.org` | Base URL of the RustChain node |
| `EXPORTER_PORT` | `9200` | Port for the Prometheus HTTP endpoint |
| `SCRAPE_INTERVAL` | `30` | Seconds between collection cycles |
| `REQUEST_TIMEOUT` | `15` | HTTP request timeout in seconds |
| `NODE_PROCESS_NAME` | `rustchain` | Process name for CPU/memory monitoring |
| `TLS_VERIFY` | `true` | Verify TLS certificates |
| `TLS_CA_BUNDLE` | _(empty)_ | Path to a custom CA bundle |
| `LOG_LEVEL` | `INFO` | Logging verbosity (DEBUG, INFO, WARNING, ERROR) |

## Docker

```bash
docker build -t rustchain-enhanced-exporter .
docker run -d \
  -p 9200:9200 \
  -e RUSTCHAIN_NODE=https://rustchain.org \
  rustchain-enhanced-exporter
```

## Prometheus Configuration

Add the following scrape target to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: "rustchain-enhanced"
    scrape_interval: 30s
    static_configs:
      - targets: ["localhost:9200"]
```

## Grafana

Import the companion dashboard from `tools/prometheus/grafana-dashboard.json`
or build panels using the metrics listed above. Recommended panels:

- **Miner Hashrate Heatmap** — `rustchain_miner_hashrate` grouped by `hardware_type`
- **TX Throughput** — `rustchain_tx_throughput_per_minute` over time
- **Fee Pool Growth** — `rustchain_fee_pool_growth_rate_rtc_per_min` trend line
- **API Latency P99** — `histogram_quantile(0.99, rate(rustchain_api_request_duration_seconds_bucket[5m]))`
- **Attestation Success** — `rustchain_attestation_success_rate` gauge
- **Peer Quality Distribution** — `rustchain_peer_quality_score` bar chart

## Dependencies

- Python 3.9+
- `prometheus-client`
- `requests`
- `psutil`

## Differences from `monitoring/rustchain-exporter.py`

The base exporter provides node health, epoch, and miner counts. This enhanced
version adds:

1. **Per-miner hashrate and uptime** with hardware labels
2. **Transaction throughput** with rolling per-minute rate and mempool depth
3. **System resource monitoring** (CPU, memory, file descriptors) via `psutil`
4. **Peer connection quality scores** and per-peer latency
5. **Attestation success/failure tracking** with rolling success rate
6. **Fee pool growth rate** computed across scrape intervals
7. **Wallet balance change deltas** between scrapes
8. **Histogram metrics** for API endpoint latency with configurable buckets
