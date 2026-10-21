# Synology updater safety review

Reviewed on 21 October 2026. This is a review of the repository's script and
supplied Compose configuration, not a certification of the live NAS. No commands
were executed on the NAS during this review.

## Conclusion

The script's intended host writes are confined to deployment configuration,
automation state/logs, database backup files, and Docker-managed image/container
storage. It does not contain system-package upgrades, user/account changes,
firewall or certificate changes, reboot commands, recursive deletion, Docker
pruning, volume removal, database restoration, or `compose down`.

That does not make unattended root execution risk-free. Docker administration
is a privileged capability, and each release runs application code and database
migrations. A defective or compromised release can damage application data;
Docker/runtime vulnerabilities can also affect the host. No test suite can
guarantee that all future releases will be harmless.

## Findings corrected

- The command search path is fixed to system/package directories rather than
  inherited from the task's environment. Docker is explicitly directed at the
  local Unix socket, rather than an inherited remote context.
- Deployment paths must be absolute, without dot traversal, contained in one
  deployment root, and non-overlapping. Common system-directory roots, symlinked
  paths/files, multiply hard-linked files, and arbitrary Compose file paths are rejected.
- The deployment root, project, automation, backup directories, Compose file,
  `.env`, updater, and existing state destinations must belong to the task user
  or root and must not be writable by group/other users. On the NAS the task
  user is root. These checks precede opening the log.
- New backup, state, temporary, and log files use a private umask. Database dumps
  use a securely created temporary file, and existing final backup targets are
  not overwritten. Temporary files are cleaned up by exact filename.
- Release tags and image digests returned from container-side parsing are
  independently constrained on the host before use in paths or image references.
  HTTPS redirects are restricted to HTTPS.
- Existing container project labels and, where available, their working-directory
  labels are checked before deployment.
- Low disk space prevents deployment. Backup output is size-limited to retain
  a measured free-space reserve; failure prevents migrations.
- `--check-only` provides a preflight run without downloading, stopping web, or
  migrating. It still creates normal logs/lock state.

## Scope of normal operations

| Operation | Scope |
| --- | --- |
| Download release metadata | Fixed public GitHub repository over HTTPS |
| Pull image | Fixed `ghcr.io/fedeceri85/flare-eln` image namespace |
| Run Django checks and migrations | Temporary `web` service containers using the pinned digest, with `--no-deps` |
| Stop/start deployment | Existing Compose project's `web` service only |
| Database backup | `pg_dump` executed in the existing `db` container; gzip output in configured backup directory |
| Persist image | Replace `.env` atomically, changing `FLARE_IMAGE` and preserving other settings |
| State and logs | Configured automation directory |
| Delete files | Exact temporary/lock files created for the update; no recursive deletion |

The supplied web configuration has no privileged flag, host Docker socket mount,
or writable host bind mount. It mounts `external-imports` read-only. PostgreSQL
has its separate data bind mount. Root-owned local configuration is trusted:
altering that Compose configuration to add privileges or dangerous mounts would
change this assessment. The script does not implement a complete Compose policy
engine.

## Failure behaviour verified

- No changed release: no image pull, restart, or migration.
- Image unavailable, invalid metadata/digest, or initial Django check fails:
  existing web stays running.
- Failed backup: no migration runs; the original web container is restarted.
- Failed migration or final application check: no success recorded; `FAILED`
  blocks subsequent automatic deployments.
- No automatic code rollback or database restoration after a migration failure.
- Core dumps are disabled for updater subprocesses to avoid writing process
  memory to unexpected files after a failure.
- Legacy Synology `docker-compose` works without `config --images`.

## Remaining limits

- Automated backups must be copied off the NAS and periodically restore-tested.
  `gzip -t` verifies compressed-stream integrity, not successful SQL restoration.
- Docker image layers and retained backups/images/logs can consume disk space.
  Free-space checks and the dump limit reduce risk but cannot reserve space
  against other processes or bound a malicious image's expansion.
- Digest pinning keeps migration and web images consistent. It is not independent
  signature verification; the public repository, GitHub build workflow, registry
  account, base image, and dependencies remain trusted.
- Permission checks cover Unix owner/mode bits, not the full Synology ACL model.
  Trusted-administrator access must also be enforced through DSM permissions.
- Concurrent manual changes to Compose, `.env`, container state, or the database
  during deployment are outside the updater's lock. Do not make those changes
  while the task is running.
- HTTP checks run inside the container. They do not verify public HTTPS, reverse
  proxy configuration, DNS, certificates, or outside connectivity.
- Tests use simulated Docker/GitHub commands, real filesystem operations,
  compression, and shell execution. They do not test Docker isolation, a live
  NAS, power-loss recovery, or restoration of production data.

Run the suite with:

```bash
python3 scripts/test_auto_update_synology_release.py
```

References:

- [Docker Engine security](https://docs.docker.com/engine/security/)
- [Docker daemon access protection](https://docs.docker.com/engine/security/protect-access/)
