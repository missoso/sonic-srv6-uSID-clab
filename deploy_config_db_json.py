#!/usr/bin/env python3
"""
Deploy config_db.json files to SONiC hosts.

Looks for files named <host>_config_db.json in the local ./configs/ folder,
copies each one to the corresponding host, replaces /etc/sonic/config_db.json,
and runs 'sudo config reload -y'.
"""

import os
import glob
import paramiko
from scp import SCPClient

CONFIGS_DIR = os.path.join(os.path.dirname(__file__), "configs")
REMOTE_TMP = "/tmp/config_db.json"
REMOTE_TARGET = "/etc/sonic/config_db.json"
USERNAME = "admin"
PASSWORD = "admin"
SSH_PORT = 22


def get_hosts():
    """Discover hosts from config files in the configs directory."""
    pattern = os.path.join(CONFIGS_DIR, "*_config_db.json")
    hosts = []
    for filepath in sorted(glob.glob(pattern)):
        filename = os.path.basename(filepath)
        host = filename.replace("_config_db.json", "")
        hosts.append((host, filepath))
    return hosts


def deploy(host, local_config_path):
    print(f"\n[{host}] Connecting...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=SSH_PORT, username=USERNAME, password=PASSWORD, timeout=30)
    except Exception as e:
        print(f"[{host}] ERROR: Could not connect — {e}")
        return False

    try:
        # Copy file to remote /tmp first
        print(f"[{host}] Uploading {local_config_path} -> {REMOTE_TMP}")
        with SCPClient(ssh.get_transport()) as scp:
            scp.put(local_config_path, REMOTE_TMP)

        # Replace /etc/sonic/config_db.json
        cmd_replace = f"sudo cp {REMOTE_TMP} {REMOTE_TARGET}"
        print(f"[{host}] Running: {cmd_replace}")
        stdin, stdout, stderr = ssh.exec_command(cmd_replace, get_pty=True)
        stdin.write(PASSWORD + "\n")
        stdin.flush()
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            print(f"[{host}] ERROR replacing config_db.json: {stderr.read().decode().strip()}")
            return False

        # Reload config
        cmd_reload = "sudo config reload -y"
        print(f"[{host}] Running: {cmd_reload}")
        stdin, stdout, stderr = ssh.exec_command(cmd_reload, get_pty=True)
        stdin.write(PASSWORD + "\n")
        stdin.flush()
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode().strip()
        if output:
            print(f"[{host}] {output}")
        if exit_code != 0:
            print(f"[{host}] ERROR during config reload: {stderr.read().decode().strip()}")
            return False

        print(f"[{host}] Done.")
        return True

    finally:
        ssh.close()


def main():
    hosts = get_hosts()
    if not hosts:
        print(f"No config files found in {CONFIGS_DIR}")
        return

    print(f"Found {len(hosts)} host(s): {[h for h, _ in hosts]}")

    results = {}
    for host, path in hosts:
        results[host] = deploy(host, path)

    print("\n--- Summary ---")
    for host, success in results.items():
        status = "OK" if success else "FAILED"
        print(f"  {host}: {status}")


if __name__ == "__main__":
    main()
