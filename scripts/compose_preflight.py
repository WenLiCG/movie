"""Verify this deployment owns the Compose project before changing containers."""
import json
from pathlib import Path
import socket
import subprocess
import sys

def output(*args):
    return subprocess.check_output(args, text=True)

config = json.loads(output('docker', 'compose', '--profile', '*', 'config', '--format', 'json'))
root, project = str(Path.cwd().resolve()), config['name']
ids = output('docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project).split()
project_containers = json.loads(output('docker', 'inspect', *ids)) if ids else []
for container in project_containers:
    labels = container.get('Config', {}).get('Labels', {}) or {}
    if labels.get('com.docker.compose.project.working_dir') != root:
        raise SystemExit('停止：另一部署目录已使用 Compose 项目名 ' + project + '；不会操作它。')
ports_to_check = sys.argv[1:]
ids = output('docker', 'ps', '-q').split() if ports_to_check else []
running = json.loads(output('docker', 'inspect', *ids)) if ids else []
conflicts = []
for service in ports_to_check:
    for port in config['services'][service].get('ports', []):
        host, number = port.get('host_ip', '0.0.0.0'), int(port['published'])
        proto = port.get('protocol', 'tcp')
        owned = False
        for container in running:
            labels = container.get('Config', {}).get('Labels', {}) or {}
            ours = labels.get('com.docker.compose.project.working_dir') == root and labels.get('com.docker.compose.service') == service
            for binding, entries in (container.get('NetworkSettings', {}).get('Ports', {}) or {}).items():
                if binding.rsplit('/', 1)[-1] != proto:
                    continue
                for entry in entries or []:
                    ip = entry.get('HostIp', '0.0.0.0')
                    overlap = ip == host or ip in ('', '0.0.0.0', '::') or host in ('', '0.0.0.0', '::')
                    if int(entry['HostPort']) == number and overlap:
                        if ours:
                            owned = True
                        else:
                            conflicts.append(f'{service}: {host}:{number}/{proto} 被容器 {container.get("Name", "")} 占用')
        if owned:
            continue
        kind = socket.SOCK_DGRAM if proto == 'udp' else socket.SOCK_STREAM
        family = socket.AF_INET6 if ':' in host else socket.AF_INET
        try:
            with socket.socket(family, kind) as probe:
                probe.bind((host, number))
        except OSError as error:
            conflicts.append(f'{service}: {host}:{number}/{proto} 无法绑定 ({error})')
if conflicts:
    raise SystemExit('停止：端口预检未通过，请先调整 compose.yaml 或已有服务：\n' + '\n'.join(conflicts))
