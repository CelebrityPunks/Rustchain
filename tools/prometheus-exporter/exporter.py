#!/usr/bin/env python3
"""
Enhanced Prometheus Exporter for RustChain

Provides deep observability into RustChain node operations including
per-miner hashrate/uptime, transaction throughput, system resource
usage, peer connection quality, attestation success rates, fee pool
growth tracking, wallet balance deltas, and API latency histograms.
"""

import logging
import os
import platform
import threading
import time
from collections import deque
from typing import Any, Optional

import psutil
import requests
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    start_http_server,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NODE_URL = os.getenv("RUSTCHAIN_NODE", "https://rustchain.org").rstrip("/")
EXPORTER_PORT = int(os.getenv("EXPORTER_PORT", "9200"))
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "30"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
NODE_PROCESS_NAME = os.getenv("NODE_PROCESS_NAME", "rustchain")
TLS_VERIFY = os.getenv("TLS_VERIFY", "true").lower() in ("true", "1", "yes")
TLS_CA_BUNDLE = os.getenv("TLS_CA_BUNDLE", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rustchain_enhanced_exporter")

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------
session = requests.Session()
session.headers.update({"Accept": "application/json"})


def _verify_arg():
    if TLS_CA_BUNDLE:
        return TLS_CA_BUNDLE
    return TLS_VERIFY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Prometheus Metrics
# ---------------------------------------------------------------------------

# --- Node health ---
node_up = Gauge(
    "rustchain_node_up", "Node reachable (1=up, 0=down)", ["version"]
)
node_uptime_seconds = Gauge(
    "rustchain_node_uptime_seconds", "Node reported uptime in seconds"
)
node_db_status = Gauge(
    "rustchain_node_db_status", "Database read/write status (1=ok)"
)
node_info = Info("rustchain_node", "Static node metadata")

# --- Epoch ---
current_epoch = Gauge("rustchain_current_epoch", "Current epoch number")
current_slot = Gauge("rustchain_current_slot", "Current slot in epoch")
epoch_pot_rtc = Gauge("rustchain_epoch_pot_rtc", "Epoch reward pot in RTC")
epoch_progress = Gauge(
    "rustchain_epoch_progress_ratio",
    "Fraction of slots elapsed in current epoch (0-1)",
)
epoch_seconds_remaining = Gauge(
    "rustchain_epoch_seconds_remaining",
    "Estimated seconds until epoch rollover",
)

# --- Per-miner metrics ---
miner_hashrate = Gauge(
    "rustchain_miner_hashrate",
    "Reported hashrate for a miner",
    ["miner", "hardware_type", "arch"],
)
miner_uptime_seconds = Gauge(
    "rustchain_miner_uptime_seconds",
    "Continuous uptime of a miner in seconds",
    ["miner"],
)
miner_last_attest_timestamp = Gauge(
    "rustchain_miner_last_attest_timestamp",
    "Unix timestamp of most recent attestation",
    ["miner", "arch"],
)
miner_antiquity_multiplier = Gauge(
    "rustchain_miner_antiquity_multiplier",
    "Antiquity multiplier assigned to miner hardware",
    ["miner", "hardware_type"],
)
active_miners_total = Gauge(
    "rustchain_active_miners_total", "Count of miners active in last 30 min"
)
enrolled_miners_total = Gauge(
    "rustchain_enrolled_miners_total", "Count of enrolled miners"
)
miners_by_hardware = Gauge(
    "rustchain_miners_by_hardware",
    "Miner count bucketed by hardware type",
    ["hardware_type"],
)
miners_by_arch = Gauge(
    "rustchain_miners_by_arch",
    "Miner count bucketed by architecture",
    ["arch"],
)
avg_antiquity_multiplier = Gauge(
    "rustchain_avg_antiquity_multiplier",
    "Mean antiquity multiplier across all miners",
)

# --- Transaction throughput ---
tx_total = Counter(
    "rustchain_transactions_total",
    "Cumulative transaction count observed",
)
tx_throughput_per_minute = Gauge(
    "rustchain_tx_throughput_per_minute",
    "Transactions processed per minute (rolling window)",
)
tx_pending = Gauge(
    "rustchain_tx_pending", "Number of transactions in the mempool"
)

# --- System resources (node process) ---
node_cpu_percent = Gauge(
    "rustchain_node_cpu_percent",
    "CPU usage of the RustChain node process (%)",
)
node_memory_rss_bytes = Gauge(
    "rustchain_node_memory_rss_bytes",
    "Resident set size of the node process in bytes",
)
node_memory_vms_bytes = Gauge(
    "rustchain_node_memory_vms_bytes",
    "Virtual memory size of the node process in bytes",
)
node_open_fds = Gauge(
    "rustchain_node_open_file_descriptors",
    "Open file descriptors held by node process",
)
system_cpu_percent = Gauge(
    "rustchain_system_cpu_percent", "Overall system CPU usage (%)"
)
system_memory_used_bytes = Gauge(
    "rustchain_system_memory_used_bytes",
    "System memory in use (bytes)",
)
system_memory_total_bytes = Gauge(
    "rustchain_system_memory_total_bytes",
    "Total system memory (bytes)",
)

# --- Peer connection quality ---
peer_count = Gauge("rustchain_peer_count", "Number of connected peers")
peer_quality_score = Gauge(
    "rustchain_peer_quality_score",
    "Connection quality score for a peer (0-100)",
    ["peer_id"],
)
avg_peer_quality = Gauge(
    "rustchain_avg_peer_quality_score",
    "Average peer connection quality across all peers",
)
peer_latency_ms = Gauge(
    "rustchain_peer_latency_ms",
    "Latency to peer in milliseconds",
    ["peer_id"],
)

# --- Attestation success rates ---
attestation_success_total = Counter(
    "rustchain_attestation_success_total",
    "Total successful attestations observed",
)
attestation_failure_total = Counter(
    "rustchain_attestation_failure_total",
    "Total failed attestations observed",
)
attestation_success_rate = Gauge(
    "rustchain_attestation_success_rate",
    "Rolling attestation success rate (0-1)",
)

# --- Fee pool growth ---
fee_pool_total_rtc = Gauge(
    "rustchain_fee_pool_total_rtc", "Total fees collected in RTC"
)
fee_pool_events_total = Gauge(
    "rustchain_fee_pool_events_total", "Total fee collection events"
)
fee_pool_growth_rate_rtc_per_min = Gauge(
    "rustchain_fee_pool_growth_rate_rtc_per_min",
    "Fee pool growth rate in RTC per minute",
)

# --- Wallet balance tracking ---
wallet_balance_rtc = Gauge(
    "rustchain_wallet_balance_rtc",
    "Current wallet balance in RTC",
    ["address"],
)
wallet_balance_delta_rtc = Gauge(
    "rustchain_wallet_balance_delta_rtc",
    "Balance change since last scrape in RTC",
    ["address"],
)

# --- Hall of Fame ---
hall_total_machines = Gauge(
    "rustchain_hall_of_fame_total_machines", "Machines in hall of fame"
)
hall_total_attestations = Gauge(
    "rustchain_hall_of_fame_total_attestations",
    "Total attestations in hall of fame",
)
hall_oldest_machine_year = Gauge(
    "rustchain_hall_of_fame_oldest_machine_year",
    "Manufacture year of oldest machine",
)
hall_highest_rust_score = Gauge(
    "rustchain_hall_of_fame_highest_rust_score", "Highest rust score"
)

# --- API latency histograms ---
api_latency = Histogram(
    "rustchain_api_request_duration_seconds",
    "Latency of API requests to the RustChain node",
    ["endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# --- Scrape meta ---
scrape_errors_total = Counter(
    "rustchain_exporter_scrape_errors_total", "Total scrape errors"
)
scrape_duration_seconds = Gauge(
    "rustchain_exporter_scrape_duration_seconds",
    "Time spent collecting metrics",
)
scrape_timestamp = Gauge(
    "rustchain_exporter_last_scrape_timestamp",
    "Unix timestamp of last completed scrape",
)

# ---------------------------------------------------------------------------
# Stateful trackers
# ---------------------------------------------------------------------------

_prev_fee_total: Optional[float] = None
_prev_fee_ts: Optional[float] = None
_prev_balances: dict[str, float] = {}
_prev_tx_count: Optional[int] = None
_tx_window: deque = deque(maxlen=60)  # (timestamp, count) pairs
_attest_ok: int = 0
_attest_fail: int = 0
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Fetch with instrumented latency
# ---------------------------------------------------------------------------


def fetch_json(endpoint: str) -> Optional[Any]:
    url = f"{NODE_URL}{endpoint}"
    try:
        start = time.monotonic()
        resp = session.get(url, timeout=REQUEST_TIMEOUT, verify=_verify_arg())
        elapsed = time.monotonic() - start
        api_latency.labels(endpoint=endpoint).observe(elapsed)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("request failed endpoint=%s error=%s", endpoint, exc)
        scrape_errors_total.inc()
        return None


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


def collect_health() -> bool:
    data = fetch_json("/health")
    if not isinstance(data, dict):
        node_up.labels(version="unknown").set(0)
        return False

    version = str(data.get("version", "unknown"))
    ok = data.get("ok", data.get("healthy", True))
    node_up.labels(version=version).set(1 if ok else 0)
    node_uptime_seconds.set(
        _float(data.get("uptime_s", data.get("uptime_seconds", 0)))
    )
    node_db_status.set(1 if data.get("db_rw", True) else 0)
    node_info.info(
        {
            "version": version,
            "node_url": NODE_URL,
            "exporter_host": platform.node(),
        }
    )
    return True


def collect_epoch() -> dict:
    data = fetch_json("/epoch")
    if not isinstance(data, dict):
        return {}

    ep = _int(data.get("epoch", data.get("current_epoch", 0)))
    slot = _int(data.get("slot", data.get("current_slot", 0)))
    slots_per = _int(data.get("slots_per_epoch", data.get("blocks_per_epoch", 0)))
    sec_per_slot = _float(
        data.get("seconds_per_slot", data.get("slot_duration_seconds", 600)), 600
    )

    current_epoch.set(ep)
    current_slot.set(slot)
    epoch_pot_rtc.set(_float(data.get("epoch_pot", 0)))

    if slots_per > 0:
        in_epoch = slot % slots_per
        epoch_progress.set(in_epoch / slots_per)
        epoch_seconds_remaining.set(max(slots_per - in_epoch, 0) * sec_per_slot)
    else:
        epoch_progress.set(0)
        epoch_seconds_remaining.set(0)

    enrolled_miners_total.set(_int(data.get("enrolled_miners", 0)))
    return data


def collect_miners() -> None:
    data = fetch_json("/api/miners")
    if not isinstance(data, list):
        active_miners_total.set(0)
        return

    now = time.time()
    active = 0
    hw_counts: dict[str, int] = {}
    arch_counts: dict[str, int] = {}
    multipliers: list[float] = []

    miner_hashrate._metrics.clear()
    miner_uptime_seconds._metrics.clear()
    miner_last_attest_timestamp._metrics.clear()
    miner_antiquity_multiplier._metrics.clear()

    for m in data:
        if not isinstance(m, dict):
            continue

        mid = str(m.get("miner", m.get("id", "unknown")))
        hw = str(m.get("hardware_type", "Unknown"))
        arch = str(m.get("arch", m.get("device_arch", "Unknown")))
        mult = _float(m.get("antiquity_multiplier", 1.0), 1.0)
        hr = _float(m.get("hashrate", m.get("hash_rate", 0)))
        up = _float(m.get("uptime_seconds", m.get("uptime", 0)))
        last_attest = _float(
            m.get("last_attest", m.get("last_attest_timestamp", 0))
        )

        miner_hashrate.labels(miner=mid, hardware_type=hw, arch=arch).set(hr)
        miner_uptime_seconds.labels(miner=mid).set(up)
        miner_last_attest_timestamp.labels(miner=mid, arch=arch).set(last_attest)
        miner_antiquity_multiplier.labels(miner=mid, hardware_type=hw).set(mult)

        hw_counts[hw] = hw_counts.get(hw, 0) + 1
        arch_counts[arch] = arch_counts.get(arch, 0) + 1
        multipliers.append(mult)

        if last_attest > 0 and (now - last_attest) <= 1800:
            active += 1

    active_miners_total.set(active)

    miners_by_hardware._metrics.clear()
    for hw, cnt in hw_counts.items():
        miners_by_hardware.labels(hardware_type=hw).set(cnt)

    miners_by_arch._metrics.clear()
    for arch, cnt in arch_counts.items():
        miners_by_arch.labels(arch=arch).set(cnt)

    if multipliers:
        avg_antiquity_multiplier.set(sum(multipliers) / len(multipliers))


def collect_transactions() -> None:
    global _prev_tx_count

    data = fetch_json("/api/stats")
    if not isinstance(data, dict):
        return

    count = _int(
        data.get("total_transactions", data.get("tx_count", None))
    )
    pending = _int(data.get("pending_transactions", data.get("mempool_size", 0)))
    tx_pending.set(pending)

    now = time.time()
    if count > 0:
        if _prev_tx_count is not None and count >= _prev_tx_count:
            delta = count - _prev_tx_count
            if delta > 0:
                tx_total.inc(delta)
            _tx_window.append((now, delta))
        _prev_tx_count = count

    # Compute rolling per-minute throughput
    cutoff = now - 60
    recent = [(ts, d) for ts, d in _tx_window if ts >= cutoff]
    tx_throughput_per_minute.set(sum(d for _, d in recent))


def collect_system_resources() -> None:
    # Per-process metrics for the node
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if NODE_PROCESS_NAME.lower() in name or NODE_PROCESS_NAME.lower() in cmdline:
                with proc.oneshot():
                    node_cpu_percent.set(proc.cpu_percent(interval=0.1))
                    mem = proc.memory_info()
                    node_memory_rss_bytes.set(mem.rss)
                    node_memory_vms_bytes.set(mem.vms)
                    try:
                        node_open_fds.set(proc.num_fds())
                    except (AttributeError, psutil.Error):
                        # num_fds not available on Windows
                        try:
                            node_open_fds.set(proc.num_handles())
                        except (AttributeError, psutil.Error):
                            pass
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # System-wide
    system_cpu_percent.set(psutil.cpu_percent(interval=0))
    mem = psutil.virtual_memory()
    system_memory_used_bytes.set(mem.used)
    system_memory_total_bytes.set(mem.total)


def collect_peers() -> None:
    data = fetch_json("/api/peers")
    if not isinstance(data, (list, dict)):
        peer_count.set(0)
        return

    peers = data if isinstance(data, list) else data.get("peers", [])
    peer_count.set(len(peers))

    peer_quality_score._metrics.clear()
    peer_latency_ms._metrics.clear()
    scores: list[float] = []

    for p in peers:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("peer_id", p.get("id", "unknown")))
        quality = _float(p.get("quality_score", p.get("score", 0)))
        latency = _float(p.get("latency_ms", p.get("latency", 0)))
        peer_quality_score.labels(peer_id=pid).set(quality)
        peer_latency_ms.labels(peer_id=pid).set(latency)
        scores.append(quality)

    if scores:
        avg_peer_quality.set(sum(scores) / len(scores))
    else:
        avg_peer_quality.set(0)


def collect_attestations() -> None:
    global _attest_ok, _attest_fail

    data = fetch_json("/api/attestations")
    if data is None:
        # Try alternative endpoint
        data = fetch_json("/api/stats")

    if not isinstance(data, dict):
        return

    ok = _int(
        data.get(
            "successful_attestations",
            data.get("attestations_success", data.get("total_attestations", 0)),
        )
    )
    fail = _int(
        data.get(
            "failed_attestations",
            data.get("attestations_failed", 0),
        )
    )

    if ok > _attest_ok:
        attestation_success_total.inc(ok - _attest_ok)
    if fail > _attest_fail:
        attestation_failure_total.inc(fail - _attest_fail)

    _attest_ok = ok
    _attest_fail = fail

    total = ok + fail
    if total > 0:
        attestation_success_rate.set(ok / total)
    else:
        attestation_success_rate.set(1.0)


def collect_fee_pool() -> None:
    global _prev_fee_total, _prev_fee_ts

    data = fetch_json("/api/fee_pool")
    if not isinstance(data, dict):
        return

    total = _float(
        data.get("total_fees_collected_rtc", data.get("total_fees", 0))
    )
    events = _float(
        data.get("fee_events_total", data.get("total_fee_events", 0))
    )

    fee_pool_total_rtc.set(total)
    fee_pool_events_total.set(events)

    now = time.time()
    if _prev_fee_total is not None and _prev_fee_ts is not None:
        elapsed_min = (now - _prev_fee_ts) / 60.0
        if elapsed_min > 0:
            rate = (total - _prev_fee_total) / elapsed_min
            fee_pool_growth_rate_rtc_per_min.set(max(rate, 0))

    _prev_fee_total = total
    _prev_fee_ts = now


def collect_wallets() -> None:
    global _prev_balances

    data = fetch_json("/api/stats")
    if not isinstance(data, dict):
        return

    balances = data.get("balances", data.get("top_balances", []))
    if not isinstance(balances, list):
        return

    wallet_balance_rtc._metrics.clear()
    wallet_balance_delta_rtc._metrics.clear()
    new_balances: dict[str, float] = {}

    for row in balances:
        if not isinstance(row, dict):
            continue
        addr = str(row.get("miner", row.get("address", "unknown")))
        bal = _float(row.get("balance_rtc", row.get("balance", 0)))

        wallet_balance_rtc.labels(address=addr).set(bal)
        new_balances[addr] = bal

        prev = _prev_balances.get(addr)
        if prev is not None:
            wallet_balance_delta_rtc.labels(address=addr).set(bal - prev)
        else:
            wallet_balance_delta_rtc.labels(address=addr).set(0)

    _prev_balances = new_balances


def collect_hall_of_fame() -> None:
    data = fetch_json("/api/hall_of_fame")
    if not isinstance(data, dict):
        return

    stats = data.get("stats", data)
    if not isinstance(stats, dict):
        stats = {}

    hall_total_machines.set(_float(stats.get("total_machines", 0)))
    hall_total_attestations.set(_float(stats.get("total_attestations", 0)))
    hall_oldest_machine_year.set(
        _float(stats.get("oldest_machine_year", stats.get("oldest_year", 0)))
    )
    hall_highest_rust_score.set(_float(stats.get("highest_rust_score", 0)))


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------


def collect_all() -> None:
    start = time.monotonic()

    health_ok = collect_health()
    collect_epoch()
    collect_miners()
    collect_transactions()
    collect_system_resources()
    collect_peers()
    collect_attestations()
    collect_fee_pool()
    collect_wallets()
    collect_hall_of_fame()

    elapsed = time.monotonic() - start
    scrape_duration_seconds.set(elapsed)
    scrape_timestamp.set(time.time())
    logger.info(
        "collection complete health_ok=%s duration=%.3fs", health_ok, elapsed
    )


def main() -> None:
    logger.info(
        "starting enhanced exporter node=%s port=%d interval=%ds",
        NODE_URL,
        EXPORTER_PORT,
        SCRAPE_INTERVAL,
    )

    start_http_server(EXPORTER_PORT)
    logger.info("prometheus http server listening on :%d", EXPORTER_PORT)

    while True:
        try:
            collect_all()
        except Exception:
            logger.exception("unhandled error during collection")
            scrape_errors_total.inc()

        time.sleep(SCRAPE_INTERVAL)


if __name__ == "__main__":
    main()
