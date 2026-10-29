# The first deployment

For the day nexovue's hosted service, or a first customer's install, gets a real server. Nothing here
has been run: no server exists (founder, 2026-10-28). Each step names what proves it.

## 1. A server

One Linux server is the first deployment, by hand in the cloud's console; infrastructure code
(OpenTofu, ADR-006 amended) is written when a second environment or a customer's cloud needs it.

| | AWS | Azure | Oracle Cloud |
|---|---|---|---|
| Machine | EC2 `m7g.xlarge` (4 vCPU, 16 GB, arm64) or `m7i.xlarge` (amd64) | `Standard_D4ps_v6` (arm64) or `D4s_v6` | `VM.Standard.A1.Flex`, 4 OCPU / 24 GB (arm64) |
| Image | Ubuntu 24.04 LTS | Ubuntu 24.04 LTS | Ubuntu 24.04 |
| Disk | gp3, 100 GB, encrypted | Premium SSD v2, 128 GB | Block volume, 100 GB |
| Firewall | security group: 22 from your address only; 80, 443 from anywhere | NSG: the same | security list: the same |
| Address | an Elastic IP | a static public IP | a reserved public IP |

Sizes are the [sizing guide](../self-hosting/sizing.md)'s smallest; check the region's current prices
before choosing.

**Proved by:** `ssh` reaches it; `free -g` shows the memory.

## 2. Docker

```bash
curl -fsSL https://get.docker.com | sudo sh     # Docker Engine and the Compose plugin
docker --version && docker compose version     # 24+ and 2.24+
```

Turn on unattended security updates (`sudo dpkg-reconfigure unattended-upgrades`).

## 3. Names

Two DNS records pointing at the server's address: the console (`app.nexovue.com`) and ingest
(`ingest.nexovue.com`). **Proved by:** `dig +short` answers the address for both.

## 4. Backups' destination

A bucket in a different account or region from the server, with a key that can only list, read, write
and delete in it, and versioning on. **Proved by:** the key can write and read one object.

## 5. Install

The newest release's kit, as [install](../self-hosting/install.md) says. For the hosted service, run it
as `sudo NEXOVUE_LAYOUT=hosted ./nexovue install`; then add `SUPER_ADMIN_*`, the payment keys and the
mail provider to `nexovue.env` ([first-accounts](first-accounts.md)), with `BACKUP_S3_*` and
`NEXOVUE_MONITORING='yes'`, and run `install` again to apply them.

**Proved by:** `nexovue status` all healthy; the console signs in over HTTPS; a span sent to the ingest
name appears; `/api/v1/system/info` names the version and the deployment.

## 6. Backups, restored

`nexovue backup`, then a restore onto a second, temporary server
([disaster recovery](disaster-recovery.md)), timed and recorded there. Delete the temporary server.

## 7. Watched

Alertmanager's receiver set to where the founder is paged (`monitoring/alertmanager.yml`); one alert
made to fire on purpose — stop the worker for three minutes — and seen to arrive. An outside uptime
check on both names: the console's `/`, and ingest's `/v1/traces`, which answers a GET with 405 —
proof that the edge and the receiver are both up.

## 8. Recorded

`docs/operations/production-readiness.md` updated with what was proved; `status.md` of the change that
did it.
