# FLARE-ELN

**FAIR Laboratory Animal Research Electronic Lab Notebook**

FLARE-ELN is a FAIR (**F**indable, **A**ccessible, **I**nteroperable, **R**eusable) electronic lab notebook for laboratory animal records, regulated procedure tracking, and experimental records.

FLARE-ELN is a Django application designed for small research-group deployments, where users access the system through a local server or NAS-hosted Docker stack. It stores structured database records alongside references to associated experimental data.

The application is designed around FAIR data-management principles and supports record-keeping workflows associated with UK Home Office regulation of animal research.

## Deployment

Two Docker Compose targets are included:

- `compose.local-stack.yml` for laptop or local testing.
- `compose.synology.yml` for deployment on a Synology NAS.

Start with:
DOCKER-INSTRUCTIONS

## License

FLARE-ELN is released under the Apache License 2.0. See `LICENSE`.
