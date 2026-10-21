# Automatic Synology updates from Docker release images

You do **not** need to revert the earlier automation changes. This version
replaces the Git-based updater and its guide with image downloads. Your existing
private-to-public copying script remains your publishing method.

The result is:

```text
Publish a stable GitHub Release
    → GitHub builds Intel and ARM64 images
    → GitHub publishes ghcr.io/fedeceri85/flare-eln:YOUR_VERSION
    → the NAS downloads the image during its hourly check
    → backup → migrations → restart → application checks
```

The NAS does not pull Git or build the app. It keeps your existing database,
production `.env`, port, and database mount. A short website interruption is
needed for the backup and migrations. A release is recorded as deployed only
when the application checks pass.

## 1. Publish the new automation files using your existing script

Commit these changes in your private project as usual, generate the updated
release, and run the private-to-public copying script you already use. You do
not need a new copying process.

The release generator now includes:

| File | Purpose |
| --- | --- |
| `.github/workflows/publish-image.yml` | Build and publish images on a GitHub Release |
| `compose.synology.images.yml` | NAS Compose template that uses a downloaded image |
| `scripts/auto_update_synology_release.sh` | NAS update script |
| `docs/automatic-synology-updates.md` | This guide |

If your copying script has its own file allowlist, add those files to it. Make
sure it includes the hidden `.github` directory.

On GitHub, open <https://github.com/fedeceri85/flare-eln> and confirm that the
public `main` branch contains `.github/workflows/publish-image.yml` **before
publishing the first release**. If it is missing, stop and correct the copy.

The workflow refuses to publish from the private repository or forks. It builds
only from the public repository's release tag. Your production passwords are
not needed by the workflow. Keep them in the NAS `.env`; never supply them as
Docker build arguments or copy production `.env` files into the public repo.

## 2. Enable GitHub Actions

In the **public** repository:

1. Open **Settings → Actions → General**.
2. Ensure GitHub Actions is enabled and that GitHub's and Docker's actions are
   allowed. If using a restricted allowlist, allow `actions/checkout` and
   `docker/setup-qemu-action`, `docker/setup-buildx-action`, `docker/login-action`,
   and `docker/build-push-action`.
3. Save any changes.

The workflow requests `contents: read` and `packages: write`, and uses GitHub's
automatically provided `GITHUB_TOKEN`. You do not create a personal access token
or put NAS credentials into GitHub.

## 3. Publish a release and wait for the image

Use your real next version wherever this guide shows `v1.2.0`.

1. Open <https://github.com/fedeceri85/flare-eln/releases>.
2. Click **Draft a new release** or **Create a new release**.
3. Create a **new tag**, for example `v1.2.0`, targeting public `main` with your
   latest files. Simple tags of letters, numbers, dots, underscores, and hyphens
   are supported, up to 128 characters.
4. Add a title and release notes.
5. Leave **Set as a pre-release** unchecked.
6. Make it the **latest release**, if that option is shown.
7. Click **Publish release**.
8. Open the repository's **Actions** tab.
9. Open **Publish release image** and wait for a green successful run. The first
   multi-architecture build can take a while.

The workflow checks Django and applies migrations to a disposable SQLite
in-memory database before publishing. This catches basic application and
migration errors; it is not a replacement for your normal tests or a rehearsal
against production PostgreSQL.

It publishes a versioned image such as:

```text
ghcr.io/fedeceri85/flare-eln:v1.2.0
```

Do not enable the NAS task until this first image has been built successfully.
Pushing to `main`, creating a tag alone, saving a release draft, or publishing a
prerelease does not trigger a production update.

## 4. Make the container package public — once

**A public repository does not automatically make a newly published GHCR package
public.** This is a separate setting.

1. Open your GitHub profile: <https://github.com/fedeceri85>.
2. Open **Packages** and select the container package **flare-eln**. It may also
   appear in the public repository's sidebar.
3. Open **Package settings**.
4. Find **Change visibility**, choose **Public**, and confirm.

The NAS can now download it without a GitHub login. You normally do this only
once for the package, rather than for every release.

If the build reports a package permission error, check that the package is
connected to `fedeceri85/flare-eln` and grants that repository Actions access.

## 5. Find the existing NAS project and processor type

If you already installed the earlier Git-based scheduled task, **disable it now**.
Replace it with the task below; do not run both updaters.

In DSM **Container Manager → Container**, note the running web container's name.
Temporarily enable SSH at **Control Panel → Terminal & SNMP → Terminal**, if needed.
Connect from your Mac using your real username and NAS address:

```bash
ssh YOUR_DSM_USERNAME@YOUR_NAS_ADDRESS
```

On the NAS:

```bash
sudo -i
uname -m
docker inspect YOUR_WEB_CONTAINER_NAME --format '{{ index .Config.Labels "com.docker.compose.project" }}'
docker inspect YOUR_WEB_CONTAINER_NAME --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
docker inspect YOUR_WEB_CONTAINER_NAME --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}'
```

Replace `YOUR_WEB_CONTAINER_NAME` with the real container name. Record the
**project name**, **working directory**, and **Compose filename**. Keep them the
same during this conversion. If the directory/config labels are empty, find the
project's directory and YAML configuration in Container Manager.

Processor results:

| `uname -m` | Image support |
| --- | --- |
| `x86_64` | Supported: Intel/AMD 64-bit |
| `aarch64` | Supported: ARM64 |
| Anything else, such as `armv7l` | Stop: this workflow does not build that architecture |

The remaining examples use this existing layout:

```text
/volume1/docker/flare-eln/project/compose.synology.yml
/volume1/docker/flare-eln/project/.env
/volume1/docker/flare-eln/postgres-data/
/volume1/docker/flare-eln/backups/
```

If your directory or YAML filename differs, substitute the real values in all
commands and in the scheduled task. If multiple Compose files are listed or you
use custom settings beyond this project's standard setup, preserve those settings
when changing the web image; do not blindly replace a customised stack.

Check that these commands are available:

```bash
command -v bash
command -v curl
command -v gzip
docker compose version
```

The updater also supports the older `docker-compose` command if the last command
is unavailable. Git, Python, and jq do not need to be installed on DSM: the
updater uses Python already present in the running web container.

If you use Synology's older **Docker** package, find the web container under
**Docker → Container** instead of Container Manager. Use the SSH inspection
commands above to identify its existing Compose project and configuration.

## 6. Upload the updater and make one Compose change

On the NAS, still as root:

```bash
mkdir -p /volume1/docker/flare-eln/automation
chmod 700 /volume1/docker/flare-eln/automation
```

Using DSM File Station, upload these files from your updated private project:

| Local file | NAS destination |
| --- | --- |
| `scripts/auto_update_synology_release.sh` | `/volume1/docker/flare-eln/automation/auto_update_synology_release.sh` |
| `compose.synology.images.yml` | `/volume1/docker/flare-eln/project/compose.synology.images.yml` |

If the old updater is already in `automation/`, replace it with the new file.
The updater stays outside the application directory and needs to be replaced
manually only if you later revise the updater itself.

Back up the NAS configuration **outside** the project directory:

```bash
cd /volume1/docker/flare-eln/project
cp -p compose.synology.yml /volume1/docker/flare-eln/automation/compose.before-images.yml
cp -p .env /volume1/docker/flare-eln/automation/.env.before-images
chmod 600 /volume1/docker/flare-eln/automation/.env.before-images
```

These are one-time backups. Do not repeat these commands after conversion and
replace your original backups. The `.env` backup contains secrets: keep it on the
NAS, inside the restricted automation directory.

If your NAS uses the standard Compose file from this project, install the new
version **under the existing filename**, so Container Manager still refers to
the correct file:

```bash
cp compose.synology.images.yml compose.synology.yml
```

For a customised Compose file, use DSM's editor instead: replace only the web
service's `build: .` line with:

```yaml
    image: "${FLARE_IMAGE:?Set FLARE_IMAGE in the NAS .env to the published image}"
```

Do not leave a `build:` setting under `web`. Keep all database settings, ports,
mounts, environment variables, networks, and any other custom settings unchanged.

In the NAS `.env`, add this line using DSM's text editor, substituting the exact
version whose image passed the GitHub build:

```text
FLARE_IMAGE=ghcr.io/fedeceri85/flare-eln:v1.2.0
```

If `FLARE_IMAGE` already exists, replace that line instead of adding a duplicate.
Keep every existing password, hostname, path, and port setting.

On the NAS:

```bash
chown root:root /volume1/docker/flare-eln/automation/auto_update_synology_release.sh
chmod 700 /volume1/docker/flare-eln/automation/auto_update_synology_release.sh
bash -n /volume1/docker/flare-eln/automation/auto_update_synology_release.sh
```

The hardened updater also checks ownership and permissions before writing files.
For the standard paths above, run the following once as root (after uploading
the script). These commands are deliberately **not recursive**; they do not
change PostgreSQL data-file ownership:

```bash
mkdir -p /volume1/docker/flare-eln/backups
chown root:root /volume1/docker/flare-eln /volume1/docker/flare-eln/project /volume1/docker/flare-eln/automation /volume1/docker/flare-eln/backups
chown root:root /volume1/docker/flare-eln/project/.env /volume1/docker/flare-eln/project/compose.synology.yml
chmod go-w /volume1/docker/flare-eln /volume1/docker/flare-eln/project
chmod 700 /volume1/docker/flare-eln/automation /volume1/docker/flare-eln/backups
chmod 600 /volume1/docker/flare-eln/project/.env /volume1/docker/flare-eln/project/compose.synology.yml
```

Use your actual paths if different. The configured project, automation, and backup
folders must be separate directories beneath `DEPLOY_ROOT` (default
`/volume1/docker/flare-eln`). Symlinks in these paths or files are rejected.
Also restrict DSM shared-folder ACL permissions to trusted administrators; the
script checks Unix ownership/mode bits, not every Synology ACL rule.

The last command should return silently. Changing the files has not yet
restarted your running website. Let the updater do the first restart, including
the database backup and migrations; do not click Build or recreate the project.

## 7. Create or replace the DSM task

1. Open **Control Panel → Task Scheduler**.
2. Choose **Create → Scheduled Task → User-defined script**, or edit your existing
   disabled FLARE-ELN updater task.
3. Name it `FLARE-ELN release image updates`.
4. Set user to **root**, which needs access to operate the Docker stack.
5. Leave the task **disabled** until the test below passes.
6. Under **Schedule**, set every day, repeating every hour throughout the day.
7. Under **Task Settings → Run command**, paste:

```bash
COMPOSE_PROJECT_NAME='YOUR_EXISTING_PROJECT_NAME' \
DEPLOY_ROOT='/volume1/docker/flare-eln' \
PROJECT_DIR='/volume1/docker/flare-eln/project' \
COMPOSE_FILE='compose.synology.yml' \
STATE_DIR='/volume1/docker/flare-eln/automation' \
BACKUP_DIR='/volume1/docker/flare-eln/backups' \
/bin/bash /volume1/docker/flare-eln/automation/auto_update_synology_release.sh
```

**Replace `YOUR_EXISTING_PROJECT_NAME` with the exact value from step 5.** Also
replace the example directory/filename values if yours differ. Do not invent a
new project name; that would select different containers.

Enable email run details for abnormal termination, and configure/test DSM email
delivery if needed. Save the task.

## 8. Run it once, then enable the schedule

First append ` --check-only` to the end of the scheduled command and click
**Run**. This validates paths, permissions, Compose configuration, and the
existing containers without downloading an image, stopping web, or migrating.
It writes the normal log/temporary lock. Look for `Preflight checks passed`.
Remove ` --check-only` before testing a real update or enabling the schedule.

Pick a time when a short website interruption is acceptable. Select the task
and click **Run**. The first image deployment runs even if the old Git-based
updater recorded the same release as deployed.

Watch its log from the NAS:

```bash
tail -f /volume1/docker/flare-eln/automation/update.log
```

It should download the image, run the Django check, stop web, create a backup,
run migrations, restart web, then report:

```text
Application HTTP and database checks passed.
SUCCESS: deployed vYOUR_VERSION (ghcr.io/fedeceri85/flare-eln@sha256:...).
```

Press Ctrl+C to stop watching the log; this does not stop the updater.

Then:

1. Open your public URL, log in, and confirm the released changes are present.
2. Confirm existing records are still there.
3. Run the task a second time: it should say `Already running ... No update needed.`
4. Enable the task's schedule.
5. Disable temporary SSH access if you normally leave it disabled.

After success, the script writes the exact downloaded image digest into the NAS
`.env` as `FLARE_IMAGE`. Container Manager/manual restarts then use the same
image as the migrations. Local HTTP and database checks do not verify your
external DNS, proxy, or TLS; opening the public URL verifies that part.

## 9. Normal releases from now on

1. Finish and test the private changes.
2. Use your existing script to publish the updated sanitised source to public `main`.
3. Publish a new stable GitHub Release with a new tag, making it latest.
4. Check the **Publish release image** workflow turns green.
5. The NAS deploys the image on its next hourly check.

If the scheduled check happens while the image is still building, the download
fails without stopping the website. The next hourly check retries. Similarly,
a network failure or private package does not stop the running website.

Do not move or reuse existing version tags. The updater follows GitHub's latest
stable release, not a floating Docker `latest` tag. Marking an older release as
latest deliberately selects that older release, which might not match the
current database schema; avoid doing this as an automatic rollback.

If a release existed before the workflow was added, publishing files to `main`
does not retroactively build its image. Prefer a new release containing the new
workflow. To retry an image build that never published successfully, use
**Actions → Publish release image → Run workflow**, select public `main`, and
enter the existing release tag. Do not rebuild successfully published version
tags with different code.

## If something goes wrong

### HTTPS reverse proxy: CSRF verification failed (403)

If Synology serves HTTPS but forwards HTTP to the container, configure the exact
browser origin, including its HTTPS scheme and nonstandard port. For example,
add to the NAS `.env`:

```text
DJANGO_CSRF_TRUSTED_ORIGINS=https://marcotti-nas:8000
```

Ensure `web.environment` in the NAS Compose file includes:

```yaml
      DJANGO_CSRF_TRUSTED_ORIGINS: "${DJANGO_CSRF_TRUSTED_ORIGINS:-}"
```

This requires an image containing the matching environment-variable support in
`labdb/settings.py`; adding the variable alone to an older image has no effect.
Publish/deploy an updated release with that support, then recreate web to apply
the configuration. Keep `LABDB_WEB_PORT` at the HTTP backend port (for example
8001) if the reverse proxy uses port 8000 for HTTPS. Reload the login page before
trying again. Do not disable CSRF protection or enable production debug mode.

### Updater failures

Read `automation/update.log` and any DSM failure email first.

If the log shows the `config` usage screen followed by `ERROR: install
compose.synology.images.yml`, you may have an older copy of this updater that
used `config --images`, which older Synology Compose versions do not support.
Replace `automation/auto_update_synology_release.sh` with the current script and
run the task again. That preflight error does not stop or change the website.

| Failure | What happens |
| --- | --- |
| GitHub API/image download fails | Website keeps running; next check retries |
| Downloaded image fails its initial Django check | Website keeps running; next check retries |
| Backup fails before migrations | Original web container is restarted; a `FAILED` marker blocks further deployments |
| Migration, restart, or final check fails | Deployment stops; a `FAILED` marker blocks further deployments |

If a deployment-stage failure occurs, disable the task. The website may be
stopped or may be running the new image without passing checks. The script does
not automatically restore a database or roll back application code.

Useful files, all inside `automation/`:

- `latest-backup`: path to the most recent completed database backup; check its
  timestamp, since a failed backup attempt leaves the preceding one listed.
- `candidate-image`: exact digest of the image being attempted.
- `previous-image`: previous web container's image reference.
- `deployed-release` and `deployed-image`: last successful release and image.
- `FAILED`: deployment requires review before retrying.

Keep the database folder and backups. Do not delete the Compose project or run
`docker compose down -v` as an update fix. The existing Synology guide covers
manual database restoration; restoring can discard data created after a backup.

After diagnosing and fixing a deployment-stage failure, make sure both existing
web and db containers are running before using the updater again. If web is
stopped, review whether the intended new image can safely run against the
current database and start that image deliberately; do not blindly start an old
image after migrations. Once ready to retry the latest release:

```bash
rm /volume1/docker/flare-eln/automation/FAILED
```

Run the task manually, verify the public website, then re-enable its schedule.
If you have an old Git-updater `FAILED` marker, review that failure before
removing it; replacing the updater does not resolve an earlier failed migration.

The `update.lock` directory prevents concurrent runs. A power failure can leave
it behind. Disable the task, confirm no updater is running in DSM's process view
(the PID is in `update.lock/pid`), then remove that PID file and empty directory.
Never remove an active lock.

Backups, downloaded images, and `update.log` accumulate. Include them in your
normal storage-retention and off-NAS backup routine. This script does not
silently delete backups or prune Docker images.

The updater requires at least 2048 MB free in Docker storage and the backup
filesystem before deployment. Its backup subprocess has a file-size limit of
half the free backup budget after that reserve. An oversized backup fails and
blocks deployment rather than consuming the entire measured free budget.
These checks cannot reserve space against other processes or bound image-layer
expansion; monitor storage and keep a backup retention policy. `MIN_FREE_MB`
can increase the reserve in the task's environment.

See the [automation safety review](automation-safety-review.md) for the audited
write scope, tests, and remaining trust assumptions.

## About private commit history

Transferring private commit history is possible, but requires auditing and
filtering every historical version of files for secrets/private information.
It is separate from image deployment and is intentionally skipped here. Your
private history stays in the private repository, and your existing public-copy
workflow continues unchanged. Deleting a password from the latest version does
not remove it from older commits.

## References

- [GitHub: publishing Docker images](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
- [Docker: multi-platform GitHub Actions images](https://docs.docker.com/build/ci/github-actions/multi-platform/)
- [GitHub Container Registry and package visibility](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [GitHub latest-release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)
- [Synology Task Scheduler](https://kb.synology.com/en-us/DSM/help/DSM/AdminCenter/system_taskscheduler?version=7)
