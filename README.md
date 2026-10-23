# FLARE-ELN

**FAIR Laboratory Animal Research Electronic Lab Notebook**

FLARE-ELN is a FAIR (**F**indable, **A**ccessible, **I**nteroperable, **R**eusable) electronic lab notebook for laboratory animal records, regulated procedure tracking, and experimental records.

FLARE-ELN is a Django application designed for small research-group deployments, where users access the system through a local server or NAS-hosted Docker stack. It stores structured database records alongside references to associated experimental data.

The application is designed around FAIR data-management principles and supports record-keeping workflows associated with UK Home Office regulation of animal research.

## Deployment

Three Docker Compose targets are included:

- [`compose.local-stack.yml`](compose.local-stack.yml) for laptop or local testing.
- [`compose.synology.yml`](compose.synology.yml) for Synology deployment with an image built from source on the NAS.
- [`compose.synology.images.yml`](compose.synology.images.yml) for Synology deployment using prebuilt release images from GitHub Container Registry.

Start with [DOCKER-INSTRUCTIONS](DOCKER-INSTRUCTIONS) for local testing or manual deployment.

### Automatic Synology updates with prebuilt images

Follow the [step-by-step setup guide](docs/automatic-synology-updates.md) to configure image publishing and automatic NAS updates.

Publishing a stable GitHub Release in the public repository triggers GitHub Actions to build Intel/AMD64 and ARM64 images, tagged with the release version:

```text
ghcr.io/fedeceri85/flare-eln:YOUR_RELEASE_TAG
```

The image-based Compose file reads the image reference from `FLARE_IMAGE` in the NAS `.env`. Keep production passwords and other deployment settings in that local file. Make the container package public once so the NAS can download images without a GitHub login.

The header displays the release tag baked into each published image. It updates automatically with the image; local development builds display `dev`.

The scheduled updater checks for the latest stable release, downloads its image, checks it, briefly stops the website, backs up PostgreSQL, runs migrations, and restarts the application. After successful HTTP and database checks, it saves the exact image digest for subsequent restarts. The NAS does not need to pull Git or build the application.

For an existing deployment, follow the guide to install the image-based configuration under the existing Compose filename and retain the existing project name, database mount, and settings. After conversion, use the scheduled updater instead of the manual build-from-source update script. Database migrations still need to run when deploying a new image.

## License

FLARE-ELN is released under the GNU Affero General Public License, version 3 only (`AGPL-3.0-only`). See [LICENSE](LICENSE).
