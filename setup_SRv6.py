#!/usr/bin/env python3
"""
SRv6 Lab Setup Script
Connects to all routers via SSH and applies the required kernel commands
in the correct order, assuming FRR config is active and BGP has converged.

Order:
  Phase 1 - All routers: sysctl seg6_enabled + locator kernel routes
  Phase 2 - Transit routers (r2, rn): seg6local End behavior
  Phase 3 - Terminator (r3): seg6local End.DT6 behavior
  Phase 4 - Test destination (r4): add test prefix to loopback
  Phase 5 - Ingress (r1): SRv6 encap route for test destination
"""

import paramiko
import time
import sys

# ─────────────────────────────────────────────────────────────
# ROUTER DEFINITIONS
# ─────────────────────────────────────────────────────────────

ROUTERS = {
    "r1": {"host": "r1", "user": "admin", "password": "admin"},
    "r2": {"host": "r2", "user": "admin", "password": "admin"},
    "rn": {"host": "rn", "user": "admin", "password": "admin"},
    "r3": {"host": "r3", "user": "admin", "password": "admin"},
    "r4": {"host": "r4", "user": "admin", "password": "admin"},
}

# ─────────────────────────────────────────────────────────────
# COMMANDS PER PHASE
# ─────────────────────────────────────────────────────────────

# Phase 1 — All routers: enable SRv6 in kernel + add locator route
PHASE1_COMMANDS = {
    "r1": [
        "sudo sysctl -w net.ipv6.conf.all.seg6_enabled=1",
        "sudo sysctl -w net.ipv6.conf.all.seg6_require_hmac=0",
        "sudo ip -6 route add fc00:0:1::/48 dev Loopback0",
    ],
    "r2": [
        "sudo sysctl -w net.ipv6.conf.all.seg6_enabled=1",
        "sudo sysctl -w net.ipv6.conf.all.seg6_require_hmac=0",
        "sudo ip -6 route add fc00:0:2::/48 dev Loopback0",
    ],
    "rn": [
        "sudo sysctl -w net.ipv6.conf.all.seg6_enabled=1",
        "sudo sysctl -w net.ipv6.conf.all.seg6_require_hmac=0",
        "sudo ip -6 route add fc00:0:5::/48 dev Loopback0",
    ],
    "r3": [
        "sudo sysctl -w net.ipv6.conf.all.seg6_enabled=1",
        "sudo sysctl -w net.ipv6.conf.all.seg6_require_hmac=0",
        "sudo ip -6 route add fc00:0:3::/48 dev Loopback0",
    ],
    "r4": [
        "sudo sysctl -w net.ipv6.conf.all.seg6_enabled=1",
        "sudo sysctl -w net.ipv6.conf.all.seg6_require_hmac=0",
        "sudo ip -6 route add fc00:0:4::/48 dev Loopback0",
    ],
}

# Phase 2 — Transit routers: seg6local End behavior
PHASE2_COMMANDS = {
    "r2": [
        "sudo ip -6 route replace fc00:0:2::/48 encap seg6local action End dev Loopback0",
    ],
    "rn": [
        "sudo ip -6 route replace fc00:0:5::/48 encap seg6local action End dev Loopback0",
    ],
}

# Phase 3 — Terminator r3: seg6local End.DT6
PHASE3_COMMANDS = {
    "r3": [
        "sudo ip -6 route replace fc00:0:3::/48 encap seg6local action End.DT6 table main dev Ethernet0",
    ],
}

# Phase 4 — r4: add test destination prefix to loopback
PHASE4_COMMANDS = {
    "r4": [
        "sudo ip -6 addr add 2001:db8:99::4/128 dev lo",
    ],
}

# Phase 5 — r1: SRv6 encap route for test destination
PHASE5_COMMANDS = {
    "r1": [
        "sudo ip -6 route add 2001:db8:99::4/128 encap seg6 mode encap segs fc00:0:2::,fc00:0:5::,fc00:0:3:: dev Ethernet0",
    ],
}

# ─────────────────────────────────────────────────────────────
# SSH HELPER
# ─────────────────────────────────────────────────────────────

def run_commands(router_name, commands, ignore_errors=False):
    """SSH into a router and run a list of commands, printing output."""
    cfg = ROUTERS[router_name]
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(cfg["host"], username=cfg["user"], password=cfg["password"], timeout=10)
        print(f"  ✓ Connected to {router_name}")

        for cmd in commands:
            print(f"    → {cmd}")
            stdin, stdout, stderr = ssh.exec_command(cmd)
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()

            if out:
                print(f"      {out}")
            if err:
                # Some errors are expected (e.g. route already exists)
                if "RTNETLINK answers: File exists" in err:
                    print(f"      (already exists, skipping)")
                elif ignore_errors:
                    print(f"      (warning): {err}")
                else:
                    print(f"      ERROR: {err}")

    except Exception as e:
        print(f"  ✗ Failed to connect to {router_name}: {e}")
        if not ignore_errors:
            sys.exit(1)
    finally:
        ssh.close()


# ─────────────────────────────────────────────────────────────
# VERIFICATION HELPER
# ─────────────────────────────────────────────────────────────

def verify(router_name, command):
    """Run a single verification command and return output."""
    cfg = ROUTERS[router_name]
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(cfg["host"], username=cfg["user"], password=cfg["password"], timeout=10)
        stdin, stdout, stderr = ssh.exec_command(command)
        return stdout.read().decode().strip()
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        ssh.close()


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("SRv6 Lab Setup Script")
    print("Assumes: FRR config active, BGP converged")
    print("=" * 60)

    # ── Phase 1 ──────────────────────────────────────────────
    print("\n[Phase 1] Enabling SRv6 in kernel + locator routes (all routers)")
    for router in ["r1", "r2", "rn", "r3", "r4"]:
        print(f"\n  {router}:")
        run_commands(router, PHASE1_COMMANDS[router], ignore_errors=True)

    # Small wait to let routes settle before installing SRv6 behaviors
    print("\n  Waiting 3s for routes to settle...")
    time.sleep(3)

    # ── Phase 2 ──────────────────────────────────────────────
    print("\n[Phase 2] Installing seg6local End behavior (r2, rn)")
    for router in ["r2", "rn"]:
        print(f"\n  {router}:")
        run_commands(router, PHASE2_COMMANDS[router])

    # ── Phase 3 ──────────────────────────────────────────────
    print("\n[Phase 3] Installing seg6local End.DT6 behavior (r3)")
    print(f"\n  r3:")
    run_commands("r3", PHASE3_COMMANDS["r3"])

    # ── Phase 4 ──────────────────────────────────────────────
    print("\n[Phase 4] Adding test destination prefix on r4 loopback")
    print(f"\n  r4:")
    run_commands("r4", PHASE4_COMMANDS["r4"], ignore_errors=True)

    # ── Phase 5 ──────────────────────────────────────────────
    print("\n[Phase 5] Installing SRv6 encap route on r1")
    print(f"\n  r1:")
    run_commands("r1", PHASE5_COMMANDS["r1"], ignore_errors=True)

    # ── Verification ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Verification")
    print("=" * 60)

    print("\n[r1] seg6_enabled:")
    print(f"  {verify('r1', 'cat /proc/sys/net/ipv6/conf/all/seg6_enabled')}")

    print("\n[r1] SRv6 encap route:")
    print(f"  {verify('r1', 'ip -6 route show 2001:db8:99::4')}")

    print("\n[r2] seg6local End route:")
    print(f"  {verify('r2', 'ip -6 route show fc00:0:2::/48')}")

    print("\n[rn] seg6local End route:")
    print(f"  {verify('rn', 'ip -6 route show fc00:0:5::/48')}")

    print("\n[r3] seg6local End.DT6 route:")
    print(f"  {verify('r3', 'ip -6 route show fc00:0:3::/48')}")

    print("\n[r4] test prefix on loopback:")
    print(f"  {verify('r4', 'ip -6 addr show dev lo | grep 99')}")

    print("\n[r1] Ping test (SRv6 path r1 -> r2 -> rn -> r3 -> r4):")
    result = verify("r1", "ping6 2001:db8:99::4 -I 2001:db8:1::1 -c 5 -W 2")
    for line in result.splitlines():
        print(f"  {line}")

    print("\n" + "=" * 60)
    print("Setup complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
