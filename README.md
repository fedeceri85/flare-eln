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

## Genotype categories

Manage genotype result choices in the Django admin under **Genotype categories**.
Administrators can add categories, edit their labels, set their display order, and
retire them by clearing **Active**. Stored codes cannot be edited and categories
cannot be deleted in the admin; existing results keep their category when it is
retired. **Unknown** remains active as the default. Changes apply to mouse forms,
bulk updates, and genotype displays without a restart.

Deployments must run `python manage.py migrate` to create and seed the category
table. The defaults include WT, Het, Hom, Cre, Flx/Flx, Flx/Null, Null/Null,
Flx/Y, Null/Y, Flx/WT, Null/WT, WT/Y, and Unknown.

Genotype results also have a separate **Uncertain** checkbox for each gene on
mouse and litter forms. Checked results display as, for example, `Het (uncertain)`.
The flag is independent of the category, including Unknown. Existing results
start unmarked. In bulk updates, uncertainty is changed using each mouse’s checkbox. Changing a
shared genotype category preserves the uncertainty flags.

## Adding a litter

Enter an optional **Prefix** and a **Numbers** list such as `1,3,5-7`. This creates
five mice with IDs formed by joining the prefix to each number. Leading zeros
are preserved. The list replaces the mouse count and starting-number controls.
Duplicate numbers and existing Mouse IDs are rejected rather than renamed.

Check **No ID available — generate IDs** to fill the visible prefix from birth
date, breeding pair and crossing. Uncheck it to enter your own prefix or leave it
blank. Review the individual rows before saving; each mouse has its own sex,
genotype, uncertainty flags and optional local identifier. Shared details and
any procedure apply to the whole batch.

**Local ID type** is shared across the litter: **Other** (the default),
**Tattoo** or **Ear clip**. Each mouse has its own **Local identifier**, stored as text so values
such as `012` and `L2R1` are preserved. Unmarked pups can have blank local
identifiers. Migration 0047 preserves existing local identifiers and classifies
nonblank values as Other, without guessing their Local ID type.

## Editing selected mice

The bulk edit form includes an **Individual mice** table showing each mouse’s
crossing, local identifier, sex and genotype. Change sex, genotype or uncertainty
in a row to update that mouse; selections can include different crossings.
Sex and local identifiers are not shared update fields. With **Update genotype**
checked, changing a shared genotype immediately fills the matching individual
rows. The individual genotype values shown are saved, including any adjustments
made after filling them. Uncertainty is controlled only on individual mice.
Refresh the rows after choosing a new shared crossing to enter its genotype calls.
Local identifiers remain optional; migration 0049 replaces empty Local ID types
with Other.

## Possible duplicate mice

Mouse IDs have an explicit status: **Temporary** for IDs created automatically,
**Assigned** for typed IDs, and **Unknown** for older or imported records whose
ID origin was not recorded. Assigning a permanent ID through the rename form
sets the status to Assigned and preserves linked records.

When adding a mouse or litter, the app warns about existing Temporary or Unknown
records with the same date of birth and crossing, even if breeding pair is blank
or different. Matches are checked across protocols. Assigned-ID records are
excluded, even when breeding pair or local identifier matches.
The warning highlights matching breeding pair, local identifier and sex and links
to each existing record and its permanent-ID form.

Use **Save as new mouse anyway** to confirm that the animals are different.
Changing form details or matching records requires a fresh review. Existing
unique Mouse ID checks still apply. Local-identifier uniqueness applies only
to Temporary/Unknown records; distinct assigned IDs can share those details.
Migration 0051 applies this restriction. Migration 0050 marks existing records Unknown without guessing from ID text and indexes birth date
and crossing for this lookup.

## License

FLARE-ELN is released under the Apache License 2.0. See `LICENSE`.
