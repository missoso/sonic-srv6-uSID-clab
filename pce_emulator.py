#!/usr/bin/env python3
"""
PCE Emulator — SONiC SRv6 uSID lab
Topology: r1 ── r2 ── rn ── r3 ── r4

Simulates what a real PCE controller would do:
  1. Hold a static topology database  (replaces BGP-LS)
  2. Compute a path as a SID list     (replaces CSPF over BGP-LS graph)
  3. Program headend via SSH          (replaces TCP 4189 + PCInitiate)
  4. Verify the forwarding entry      (replaces PCRpt confirmation)

Physical lab mapping:
  - Topology DB   → BGP-LS Node / Link / Prefix NLRIs
  - compute_path  → CSPF / Dijkstra over live topology graph
  - ssh_exec      → PCInitiate / PCUpd over stateful PCEP session
  - verify_route  → PCRpt message sent back by PCC
"""

import sys
import time
import paramiko


# ──────────────────────────────────────────────────────────────────
# SSH CONFIG
# In a real PCE the southbound is a PCEP session (TCP 4189).
# Here we SSH into the SONiC box and run Linux commands directly.
# To migrate to physical: replace ssh_exec() with a PCEP client call.
# ──────────────────────────────────────────────────────────────────

SSH_CREDENTIALS = {
    "r1": {"host": "r1", "port": 22, "username": "admin", "password": "admin"},
}


# ──────────────────────────────────────────────────────────────────
# TOPOLOGY DATABASE  (replaces BGP-LS in a real PCE)
#
# In a physical lab this dict is built dynamically by parsing
# BGP-LS Node NLRIs (node identity + SID/locator) and
# BGP-LS Link NLRIs (adjacencies + metrics).
# ──────────────────────────────────────────────────────────────────

NODES = {
    "r1": {"loopback": "2001:db8:1::1/128", "usid": "fc00:0:1::", "asn": 65001},
    "r2": {"loopback": "2001:db8:2::1/128", "usid": "fc00:0:2::", "asn": 65002},
    "rn": {"loopback": "2001:db8:5::1/128", "usid": "fc00:0:5::", "asn": 65005},
    "r3": {"loopback": "2001:db8:3::1/128", "usid": "fc00:0:3::", "asn": 65003},
    "r4": {"loopback": "2001:db8:4::1/128", "usid": "fc00:0:4::", "asn": 65004},
}

# Ordered path — in a real PCE this comes from graph traversal
NODE_ORDER = ["r1", "r2", "rn", "r3", "r4"]

LINKS = [
    {"from": "r1", "to": "r2", "iface_from": "Ethernet0", "iface_to": "Ethernet0"},
    {"from": "r2", "to": "rn", "iface_from": "Ethernet4", "iface_to": "Ethernet0"},
    {"from": "rn", "to": "r3", "iface_from": "Ethernet4", "iface_to": "Ethernet0"},
    {"from": "r3", "to": "r4", "iface_from": "Ethernet4", "iface_to": "Ethernet0"},
]


# ──────────────────────────────────────────────────────────────────
# PATH COMPUTATION  (replaces CSPF in a real PCE)
#
# Returns the SID list (ERO) for the path src → dst.
#
# SID list rules for SRv6 uSID:
#   - The headend (src) is NOT included — it performs encapsulation
#   - Transit nodes ARE included as uSIDs
#   - The egress node (dst) is NOT included — the last transit node's
#     uN behavior shifts the active uSID and delivers to dst via a
#     normal IPv6 FIB lookup on the destination prefix
#
# So for r1 → r4:
#   full path  : r1 → r2 → rn → r3 → r4
#   SID list   : fc00:0:2:: , fc00:0:5:: , fc00:0:3::
#   r4 omitted : r3's uN delivers the packet, r4 does FIB lookup
# ──────────────────────────────────────────────────────────────────

def compute_path(src: str, dst: str) -> list:
    """
    Build the uSID list for src → dst.
    Excludes the headend (src) and the egress node (dst).
    In a real PCE this is the output of CSPF over the BGP-LS graph.
    """
    print(f"\n[PCE] Computing path: {src} → {dst}")

    src_idx = NODE_ORDER.index(src)
    dst_idx = NODE_ORDER.index(dst)

    if src_idx >= dst_idx:
        raise ValueError(f"No forward path from {src} to {dst}")

    # Transit nodes only: everything after src and before dst (inclusive of
    # intermediate nodes but NOT the egress dst itself)
    transit_nodes = NODE_ORDER[src_idx + 1 : dst_idx]
    sid_list = [NODES[n]["usid"] for n in transit_nodes]

    print(f"[PCE] Full path    : {' → '.join(NODE_ORDER[src_idx:dst_idx+1])}")
    print(f"[PCE] Transit nodes: {' → '.join(transit_nodes)}")
    print(f"[PCE] SID list     : {', '.join(sid_list)}")
    print(f"[PCE] Note: {dst} ({NODES[dst]['usid']}) excluded — "
          f"delivered by {transit_nodes[-1]}'s uN behavior")

    return sid_list


# ──────────────────────────────────────────────────────────────────
# SOUTHBOUND — SSH transport
# Replaces TCP 4189 PCEP session in a physical lab.
# To migrate: swap ssh_exec() for pcep_client.send_pcinitiate(ero=sid_list)
# ──────────────────────────────────────────────────────────────────

def get_ssh_client(node: str) -> paramiko.SSHClient:
    """Open an SSH session to the node."""
    creds = SSH_CREDENTIALS[node]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"\n[PCE] Opening SSH session to {node} ({creds['host']}:{creds['port']})")
    client.connect(
        hostname=creds["host"],
        port=creds["port"],
        username=creds["username"],
        password=creds["password"],
        look_for_keys=False,
        allow_agent=False,
    )
    print(f"[PCE] SSH session established")
    return client


def ssh_exec(client: paramiko.SSHClient, cmd: str) -> tuple:
    """Execute a command over the open SSH session."""
    stdin, stdout, stderr = client.exec_command(cmd)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return rc, out, err


def remove_route(client: paramiko.SSHClient, prefix: str, iface: str):
    """
    Remove existing SRv6 route for this prefix.
    In real PCEP a new PCUpd implicitly replaces the existing LSP —
    there is no explicit delete step. We do it explicitly here for
    clean state since we are working directly with the kernel table.
    """
    print(f"\n[PCE] Removing existing route for {prefix}")
    rc, out, err = ssh_exec(client, f"sudo ip -6 route del {prefix} dev {iface}")
    if rc == 0:
        print(f"[PCE] Existing route removed")
    else:
        if "No such process" in err or rc == 2:
            print(f"[PCE] No existing route — nothing to remove")
        else:
            print(f"[PCE] Warning: {err}")


def install_route(client: paramiko.SSHClient, prefix: str,
                  sid_list: list, iface: str):
    """
    Install the SRv6 encapsulation route on the headend.

    This is the PCInitiate equivalent. In a physical lab:
      - The PCE sends a PCInitiate message over TCP 4189
      - The message contains an ERO with the SID list
      - The router's PCC programs its own forwarding plane
      - No SSH or CLI involved on the PCE side

    The ip -6 route command mirrors exactly what was manually
    configured in the original lab, now driven programmatically.
    """
    segs = ",".join(sid_list)
    cmd = (f"sudo ip -6 route add {prefix} "
           f"encap seg6 mode encap segs {segs} dev {iface}")

    print(f"\n[PCE] Installing route (PCInitiate equivalent)")
    print(f"[PCE] Command: {cmd}")

    rc, out, err = ssh_exec(client, cmd)

    if rc == 0:
        print(f"[PCE] Route installed successfully")
    else:
        print(f"[PCE] ERROR: {err}")
        sys.exit(1)


def verify_route(client: paramiko.SSHClient, prefix: str) -> bool:
    """
    Confirm the forwarding entry is in the kernel table.
    Equivalent to the PCRpt message a real PCC sends back to the PCE
    after successfully programming a delegated LSP.
    """
    print(f"\n[PCE] Verifying route (PCRpt equivalent)")
    rc, out, err = ssh_exec(client, f"ip -6 route show {prefix}")

    if rc == 0 and "seg6" in out:
        print(f"[PCE] Verification OK — entry confirmed in forwarding table:")
        for line in out.splitlines():
            print(f"      {line}")
        return True
    else:
        print(f"[PCE] Verification FAILED")
        print(f"      stdout : {out}")
        print(f"      stderr : {err}")
        return False


# ──────────────────────────────────────────────────────────────────
# MAIN — PCE session lifecycle
# ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 62)
    print("  PCE Emulator — SRv6 uSID path programming")
    print("  Topology: r1 ── r2 ── rn ── r3 ── r4")
    print("=" * 62)

    headend = "r1"
    dst     = "r4"
    prefix  = "2001:db8:99::4/128"
    iface   = "Ethernet0"

    print(f"\n[PCE] Headend : {headend}  ({NODES[headend]['usid']})")
    print(f"[PCE] Dest    : {dst}      ({NODES[dst]['usid']})")
    print(f"[PCE] Prefix  : {prefix}")

    # Step 1 — compute path → SID list (ERO)
    sid_list = compute_path(headend, dst)

    # Step 2 — open southbound session to headend
    client = get_ssh_client(headend)

    try:
        # Step 3 — clean slate
        remove_route(client, prefix, iface)
        time.sleep(0.5)

        # Step 4 — program the path
        install_route(client, prefix, sid_list, iface)

        # Step 5 — confirm installation
        success = verify_route(client, prefix)

    finally:
        client.close()
        print(f"\n[PCE] SSH session to {headend} closed")

    print("\n" + "=" * 62)
    if success:
        print("  Path programmed — dataplane updated")
        print(f"  Traffic to {prefix} will follow:")
        path_str = " → ".join(["r1"] +
                   [n for n in NODE_ORDER[1:-1]] + ["r4"])
        print(f"  {path_str}")
    else:
        print("  Path programming FAILED")
    print("=" * 62)


if __name__ == "__main__":
    main()
