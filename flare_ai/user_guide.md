# FLARE-ELN user guide

Reference for the website in this repository, not live laboratory data. Labels
in bold correspond to visible controls. Relative links refer to FLARE-ELN, not
LM Studio. Examples are fictional. Update this manual when the website changes;
a deployed version may differ. Source files at the end of each topic document
where its instructions were verified.

## overview
### Navigation, tables and forms

FLARE-ELN is species-generic: use **animals** unless a specific species is
established by the user or the records. Legacy code names such as `Mouse`,
`mouse_id`, `search_mice` and the `/mice/` URL do not restrict records to mice.

Sign in with your website account. The navigation has **Home**, **Animals**,
**Procedures**, **Projects**, **Services**, **Reports**, **Lines & Crossings**
and **Settings**. **Enter code** sits beside your username. Staff also see
**Admin**. **Log out** ends your website session. Breadcrumbs and **Back to …**
return to the surrounding page.

- [Animals](/mice/) lists animals across protocols, including older records
  without a protocol.
- [Procedures](/procedures/) groups animals by project licence and protocol.
  The Home card **Experimental animals and procedures** opens this area.
- [Projects](/projects/) organises research as Project → Experiment → Recording.
  A regulatory project licence is separate from a research Project.
- [Services](/services/) contains lab-wide service experiments, such as
  genotyping when configured. A service card opens its recordings.
- [Reports](/reports/) includes the Home Office Return and an Animals shortcut.
- [Lines & Crossings](/lines-crossings/) is the genetic catalogue. All signed-in
  users can browse; staff can manage it.
- [Settings](/settings/) contains import templates, family maintenance, and
  additional staff configuration.

On lists, enter filters and press **Apply filters**; **Clear filters** resets
them. Some lists first require **Show filters**; **Hide filters** collapses the
controls. Linked column headings change sorting. **Rows** controls page size
where offered; pagination opens further results. The count is the displayed
range and total, not proof that every matching record is currently displayed.
Check filters and status tabs when something appears missing.

Tick row checkboxes to select records. The header checkbox or **Select all**
selects displayed rows, not every record on every page. **Clear selection**
undoes it; Shift-click selects a range. Verify the selection count. On Animals,
selecting rows changes **Add animals** to **Edit selected**. On Procedures it
changes to **View selected**, which opens a summary first. Clear selection to
create new animals instead.

Long table Notes and Recording paths have **Show more**/**Show less**. Hidden
table columns do not imply that stored values are absent.

On forms, follow field labels and help text. Date widgets use the browser's
date control; dates are commonly displayed as day/month/year. Tabs organise one
form: switching tabs does not save it. The main save action also appears in the
page action bar on relevant forms. **Cancel** leaves without submitting that
form. Read the error summary and field errors after a failed save; animal tabs
can show **contains errors**. Existing procedure edits and image management
have separate saves; cancelling the main form does not undo those operations.

Detailed topics: animal_ids, add_animals, edit_animals, genetics, parentage,
procedures, projects, recordings, files_and_images, spreadsheet_import, reports,
sharing and settings. The assistant explains these workflows and can prepare a
temporary Add animals draft from a conversation. The returned link opens the
normal populated form for review; the assistant cannot click Save, submit the
form or create records. Use search_mice for its supported live animal searches.
This manual cannot establish which laboratory records or files actually exist.

Sources: lab/navigation.py; lab/ui.py; lab/templates/lab/base.html;
lab/templates/lab/components/filter_form.html; lab/templates/lab/components/table.html;
lab/static/lab/app.js; lab/static/lab/table_text.js.

## animal_ids
### Animal ID (mouse_id) versus Local identifier

**Animal ID** is the unique identity of an animal record in FLARE-ELN. The model
calls it `mouse_id`; this legacy name means **Animal ID** for any species,
not just mice. It links procedures, genotypes and recordings to that animal. Every
animal needs one; two records cannot share it. An assigned ID can come from
your animal management system, but FLARE-ELN does not automatically retrieve
records from Labtrack.

**Local identifier** is an optional marking such as a tattoo or ear clip.
**Local ID type** is **Other**, **Tattoo** or **Ear clip**. Leave the identifier
blank for an unmarked animal. The legacy model field is named tattoo even for
other marking types. A marking such as `3` can recur in different litters; it
is not globally unique. Use birth date, breeding pair and crossing to distinguish
them. Matching local markings do not prove two references mean the same animal.

Example: Animal ID `LAB-00123`, Local ID type **Ear clip**, Local identifier `3`.
Use the unique Animal ID when linking that animal to a recording. When importing
local markings, explicitly confirm the actual animal match.

In **Add animals**, each Animal ID is **Prefix** directly followed by an entry
in **Numbers**. Prefix `LAB-` and Numbers `001,002` gives `LAB-001`, `LAB-002`;
leading zeros are preserved. For one arbitrary ID put its full value in Prefix
and leave Numbers blank. Prefix can be blank for complete numeric IDs in
Numbers. Numbers is not the local marking field: enter markings in the animal
rows separately.

**No ID available — generate IDs** generates a prefix from birth date, crossing
and breeding pair, then appends each Number. These IDs are **Temporary**.
Manually entered IDs are **Assigned**; older records can have status **Unknown**.
ID status differs from animal status **Alive/Culled**. Temporary IDs work for
ELN links and can later be renamed to assigned IDs.

To rename, open an Animal summary → **Edit animals** → **Animal IDs**. Edit the
ID, tick **Confirm Animal ID changes**, then **Save changes**. Procedures,
genotypes, recording links and share identities are retained; the new ID becomes
Assigned. IDs must be nonempty, unique and at most 100 characters. Editing birth
date or a local marking is separate from renaming an Animal ID.

Sources: lab/models.py (Mouse); lab/forms/common.py; lab/forms/litter.py;
lab/forms/bulk_mice.py; lab/utils.py;
lab/templates/lab/components/mouse_ids_editor.html.

## add_animals
### Add one animal or several animals

1. Open [Animals](/mice/) → **Add animals**, with no rows selected. Alternatively,
   open **Procedures**, choose the licence and protocol, then **Add animals**;
   that route preselects the protocol.
2. In **Animal IDs**, enter **Prefix** and **Numbers**. Prefix `LAB-` and Numbers
   `1,3,5-7` creates five animals: LAB-1, LAB-3, LAB-5, LAB-6, LAB-7. For one
   arbitrary ID, put the full ID in Prefix and leave Numbers blank. Ranges must
   ascend; repeated numbers and existing IDs are rejected. Maximum: 500 animals
   per batch.
3. If you lack assigned IDs, check **No ID available — generate IDs**. Supply
   **Date of birth**, **Breeding pair**, an existing **Crossing**, and Numbers.
   The generated prefix is read-only while checked; uncheck to enter your own.
4. In **Animal details**, fill shared origin, animal status and protocol values.
   **Protocol** is required even when no regulated procedures are planned.
   Choose the configured appropriate protocol. **Protocol start date** differs
   from birth date. **Max severity** and **Cull date** describe the animal.
   Expand **Notes (optional)** for shared notes.
5. Select an existing **Crossing** from suggestions. Fill each animal's Local
   identifier, Sex and per-line genotype. Multiple animals use **Genotype
   details**; for one animal these controls sit within **Animal details**.
   Crossing is required for new animals. Local markings are optional in manual
   creation, including generated-ID mode; spreadsheet generated IDs have
   additional requirements.
6. Expand **Individual animal overrides** for animals differing from shared
   values. Open an animal and check the specific fields to override. Unchecked
   fields follow shared values; checked blanks clear optional values.
7. **Parents and offspring** accepts optional mother/father IDs and individual
   overrides. **Procedures** builds an optional pending list if the protocol
   permits it. Save animals before adding **Experimental records**.
8. Review all tabs and press **Add animals** to commit. If a possible duplicate
   warning appears, compare existing records first. **Save as new animal anyway**
   confirms a genuinely different animal. It does not bypass errors such as an
   already-used Animal ID.

Rows normally refresh when IDs or crossing change. If this fails, use **Retry
updating animal rows**. Without JavaScript, use **Update animal rows** before
saving after such changes. Verify the previewed animals, markings and genotypes.

Sources: lab/forms/litter.py; lab/forms/genetics.py; lab/utils.py;
lab/templates/lab/add_animals.html; lab/templates/lab/components/litter_individual_editor.html;
lab/templates/lab/components/possible_duplicate_warning.html.

## edit_animals
### View and edit animals, including bulk changes

Click an Animal ID in **Animals** or **Procedures** to open its **Animal
summary**. For several animals, tick rows in Animals → **Edit selected**, or
Procedures → **View selected** → **Edit animals**. A summary also offers
**Share animal(s)** and **Generate report**.

The summary has **Animal details**, **Genotype details** for multiple animals,
**Parents and offspring**, **Procedures**, and **Experimental records**. Expand
differing summary values to see individual details. Multiple-animal summaries
normally start on Genotype details; single-animal summaries embed genetics in
Animal details. Editing also offers **Animal IDs**. Check **Selected animals**
before applying shared changes.

1. In **Animal details**, check each shared field you intend to update and enter
   its value. Unchecked fields stay unchanged. A checked blank clears an optional
   value; required fields still need values.
2. **Notes** offers **Leave unchanged**, **Append** and **Replace**. Append adds
   text; Replace overwrites existing notes, and Replace with blank clears them.
3. Use checked **Individual animal overrides** for values differing per animal;
   they take precedence. Local identifiers, sex and per-line genotypes are in
   the animal table (embedded in Animal details for one animal).
4. Use **Genotype details** for shared/individual genotype choices. A crossing
   change requires its update checkbox. Use **Parents and offspring** for
   explicitly selected parent changes and **Animal IDs** plus **Confirm Animal
   ID changes** for renames.
5. Press **Save changes**. This applies animal changes and pending new procedures.
   Existing procedure changes have their own preview/save workflow.

**Delete selected** opens a separate confirmation page. Review the animals and
impact before completing **Delete selected animals**. Associated procedures
are deleted; recording links are removed, while recordings remain. Deletion is
permanent and must be done by the user, not the guide tool.

Sources: lab/templates/lab/animal_summary.html; lab/templates/lab/edit_selected_mice.html;
lab/templates/lab/delete_selected_mice.html; lab/forms/bulk_mice.py;
lab/templates/lab/components/individual_animal_overrides.html; lab/static/lab/app.js.

## genetics
### Lines, crossings and genotype results

A **Line** is a genetic catalogue entry, with aliases for alternative names.
A **Crossing** is an approved combination of lines in one species. It determines
the animal's per-line genotype fields. Crossing describes the background, not
the actual genotype result: animals from one crossing can have different calls.
Use the configured genotype categories rather than assuming universal labels.

In animal creation/editing, type in **Crossing** and select a suggestion.
Suggestions include constituent line aliases. Arbitrary typed text does not
register a crossing. Multiple animals use **Genotype details**; one animal has
genetics within **Animal details**.

Each line starts with **Select individually** checked: enter results per animal.
Uncheck it to choose a shared result for that line. The result immediately fills
matching rows and disables their individual cells. Check it again to edit them
separately; the last displayed results remain. Other lines are unaffected.
On existing animals **Leave unchanged** preserves separate values. Mark
**Uncertain** where appropriate. A crossing change requires its update checkbox;
individual crossing overrides take precedence. Changing crossing can remove
genotype calls for lines outside the new crossing.

Browse [Lines & Crossings](/lines-crossings/) with **Species**, **Name**, the
**Lines**/**Crossings** tabs and letter index. Management requires staff:

- **Add lines** opens a panel. Select Species, enter comma-separated **Line
  names**, optional **Aliases**, and **These are wild-type/reference lines**
  if applicable. Press **Add lines** in the panel to save.
- **Create crossing** opens a panel. Select Species, use **Line search**, select
  the intended lines, then **Create crossing**. All must belong to that species;
  single-line crossings are supported.
- A line's **Edit** changes its name/aliases with **Save changes**. A crossing
  with multiple lines offers **Edit order**, **Move up**/**Move down** and
  **Save order**; this changes line order, not membership.

Ask staff to register missing entries rather than choosing a vaguely similar
line. Genotype labels are configured in **Settings → Genotype categories**.
Imported genotypes update the animal's current calls; recordings do not hold
separate historical genotype snapshots.

Sources: docs/animal-genotypes.md; lab/forms/genetics.py; lab/forms/bulk_mice.py;
lab/templates/lab/lines_crossings_page.html; lab/views/genetics.py.

## parentage
### Parents, offspring and matching recorded parent IDs

Open **Parents and offspring** in an animal add/edit form. Mother and father
are optional: enter external text without creating a parent record, or link an
existing ELN animal. The website does not retrieve external parent information
from Labtrack.

Type for suggestions and select the intended parent. Leaving the field or
pressing Enter resolves a unique exact Animal ID, ignoring case and surrounding
whitespace. Enter checks without submitting the form. Partial/ambiguous text
stays external unless a suggestion is selected. Local markings are not used to
establish parent identity.

New animals can share parents or have individual overrides. Existing-animal
editing groups by current parent pair; check **Update mother/father** for the
role being changed. Individual overrides take precedence. **Remove parent**
asks for confirmation and stages removal until **Save changes**. Checked blank
values clear parents. Self-parenting, the same linked animal in both roles and
ancestry cycles are blocked. Sex/birth-date conflicts can produce warnings.

The summary's **Parents and offspring** tab loads confirmed parents and
offspring. Linked IDs open summaries; **View parents** opens the linked parent
selection. **View all** opens an offspring group; **View all offspring** opens
the whole recorded set. This is only confirmed ELN parentage, not a complete
colony history. Shared external text does not establish sibling identity.
If loading fails, use **Retry loading parents and offspring**.

If parents were added later, go to **Settings → Maintenance** → **Find matching
parents**. Scanning changes nothing. Review matches, uncheck unwanted ones and
press **Link selected parents**. Eligible matches across all pages start
selected; Previous/Next retains choices, and the final action covers selections
on every page. Unique exact Animal ID matches are offered; ambiguous/missing
parents stay text. Original text is preserved and confirmed links are not
overwritten. Scans expire after one hour; scan again if records changed.

Sources: docs/animal-parentage.md; lab/templates/lab/maintenance.html;
lab/templates/lab/components/parent_editor.html; lab/templates/lab/animal_summary.html.

## procedures
### Add and edit animal procedures

Open [Procedures](/procedures/), choose the licence and protocol tabs, and find
the animals. The matrix shows their procedures by type. **View all animals**
opens the ungrouped list. Tick animals → **View selected** → **Edit animals**,
or open one Animal ID → **Edit animals**. Open **Procedures**.

Under **Add new procedure**, enter **Procedure type**, **Date**, **Lab member**
and **Lab-book page**, then **Add procedure**. Repeat for further entries.
These are pending until the main **Save changes**, which creates one procedure
per entry for every selected animal. It does not replace an existing procedure
of the same type. During animal creation, the final **Add animals** saves them
instead. Review/edit/remove pending entries before the final save.

Disabled fields can mean the selected protocol does not allow regulated
procedures. Use the correct protocol; do not change it just to bypass the
restriction. Moving an animal to a protocol that disallows regulated procedures
requires removing existing procedures first.

For existing procedures, tick the exact procedure rows and the fields to change.
Enter new values → **Preview procedure changes** → review Before/After →
**Save procedure changes**. Checked blanks clear optional dates, lab members
or lab-book pages. Previews expire after one hour. These edits save separately
from animal details. A procedure row's **Edit** opens an individual editor with
**Save procedure**. **Delete selected procedures** has its own confirmation and
targets the selected procedure records only.

The read-only summary lists procedures; use **Edit animals** to change them.

Sources: lab/templates/lab/procedure_page.html; lab/forms/procedures.py;
lab/templates/lab/components/litter_procedure_editor.html;
lab/templates/lab/components/existing_procedure_editor.html; lab/forms/mice.py.

## projects
### Projects, experiments and services

**Project → Experiment → Recording** is the research hierarchy. A Project groups
research; an Experiment belongs to a Project and selects the recording type;
a Recording is one dated entry with metadata, optional animals, notes and paths.
A regulatory project licence is separate from a research Project.

1. Open [Projects](/projects/) → **Add a project**. Enter Name, Lab member,
   Description, Start date, End date and Active as applicable, then **Save
   project**. End date must not precede start date.
2. Open the Project → **Add an experiment**. Fill its name/description/dates,
   choose a registered active **Experiment type**, then **Save experiment**.
   If a typed name matches several types, choose the full name or keep typing.
3. Open the Experiment → **Add one recording** or **Import from spreadsheet**.
   Its type determines the recording fields, units, defaults and validation.

Projects have **Active**/**Inactive** tabs; Experiments have **Current**/**Archived**.
Use the appropriate tab for older items. Row **Edit** links open the forms;
**Save changes** applies edits. Inactive/Archived are status changes, not record
deletions. An Experiment's type is locked once it contains recordings.

[Services](/services/) holds lab-wide experiments. Select a service card, such
as Genotyping if configured, to use the same recording/import workflows. Service
names depend on configuration. Staff register experiment types through
**Settings → Recording types**.

Sources: lab/forms/projects.py; lab/views/projects.py; lab/ui.py;
lab/templates/lab/projects_page.html; lab/templates/lab/experiments_page.html;
lab/templates/lab/services_page.html.

## recordings
### Create, edit and find recordings

1. Open **Projects** → Project → Experiment, or a **Services** card, then
   **Add one recording**.
2. Enter **Date**, required type-specific fields, optional **Recording paths**
   and **Notes**. Date is required. Other required fields and units depend on
   the configured recording type.
3. Under **Animals**, press **Add animals**. In the selector, filter/find
   animals, tick rows and press **Add selected animals** to return. That
   selector also offers **Add animals** to create missing records. Verify
   the selected Animal IDs.
4. Optionally attach images under **Media**, then **Save recording**.

Animals are generally optional. **Genotyping is an exception**: it needs at
least one animal; each must have a crossing, and all must share one crossing.
The genotyping crossing is inferred. Use separate records for different
crossings. Linking animals does not copy them or create genotype snapshots.

The table's **View** opens the editor for recordings you may modify. Edit fields
or the animal selection → **Save changes**. Only the owner or staff can modify
a recording, so another user's View action may be absent. **Share** provides a
code/link. **Images (N)** opens registered image attachments. Expand multi-animal
cells for IDs; **View all** opens their combined summary.

Use **Show filters** → available fields → **Apply filters**. Fields vary by
recording type. Notes/paths can be hidden in tables but still available in the
form. Long text has **Show more**/**Show less**. **Export recordings** opens
export options. To delete, tick recording rows → **Delete selected** → review
and complete the separate confirmation. Only permitted recordings can be
deleted. Deleting a recording does not delete its animals.

An animal editor's **Experimental records** also offers service **Add record**
links. Its **Add recording** row lets you choose Recording type → Project →
Experiment, then **Add record** to open a compatible form with those animals.
Only available compatible destinations appear. Save new animals first. Existing
records in that tab offer **See in Experiment** to open their originating list.

Sources: lab/forms/recordings.py; lab/models.py (Recording.can_be_modified_by);
lab/templates/lab/recordings_page.html; lab/templates/lab/components/recording_form_fields.html;
lab/templates/lab/select_recording_mouse.html; lab/templates/lab/components/new_animal_recording.html;
lab/templates/lab/edit_recording.html; lab/ui.py.

## files_and_images
### Recording paths and attached images

**Recording paths** stores text references to raw files/folders, typically on
the NAS. Enter one path per line → **Save recording** or **Save changes**.
This does not upload, copy, analyse or open those files, scan folders or verify
all files exist. A path only works on machines with access to that location;
Mac, Windows and container mount paths can differ.

Images are separate attachments. When adding a recording, use **Media** →
**Browse images** or drag/drop, then save. For an existing recording, open
**View** → **Manage images**, or the list's **Images (N)**. Open **Add images**,
choose files and **Upload images** where shown. Owners/staff can manage images;
other signed-in users can view available ones.

Accepted formats: JPEG, PNG, WebP, TIFF; up to 10 at a time, 20 MB each. Stored
files are compressed viewing copies. Multipage TIFFs use only the first page.
Keep original scientific images separately: attachments are not an archive of
full-resolution TIFFs/stacks. The gallery has **Previous**, **Next**, **Enlarge**,
**Download**, **Close** and, for permitted users, **Remove image**. Download
returns the stored viewing copy. Image operations save separately from the
recording form. Counts reflect registered attachments; availability is checked
on opening.

In spreadsheet imports, **Import settings → Media** attaches shared images to
every resulting recording. Check that this is intended.

Current AI tools do not index NAS folders, inspect TIFF/XML/ABF contents, detect
Mask.tif analysis outputs or execute Python. A folder path alone is not evidence
that a specific analysis file exists inside it.

Sources: lab/forms/recordings.py; lab/forms/recording_images.py;
lab/templates/lab/components/media_uploader.html;
lab/templates/lab/components/recording_image_dialog.html;
lab/templates/lab/edit_recording.html; lab/views/recording_images.py.

## spreadsheet_import
### Import recordings and animals from Excel or CSV

Open an Experiment's Recordings page → **Import from spreadsheet**. It needs a
recording type. Choose `.xlsx` or UTF-8 CSV → **Preview spreadsheet**. Limits:
10 MB upload, 50 MB expanded workbook, 20 worksheets, 5,000 rows/100 columns per
sheet. Formulas use cached values saved by Excel; the importer does not calculate
them. Drafts belong to the uploader/Experiment and expire after 24 hours.

Follow these seven tabs:

1. **Source & rows**: choose worksheet, header and rows. **Update preview / save
   selection** refreshes. **Select data rows**/**Clear selection** adjusts rows;
   flagged metadata starts unselected. Changing sheet/header resets mappings
   and animal resolutions. Press **Save and configure import**.
2. **Import settings**: set date interpretation, separate-row/combined layout,
   identifier rules, and shared animal Protocol, species, Local ID type and
   other values. **Distinguish reused local identifiers** helps separate repeated
   markings. Check default dates, which may initially be today. Optional Media
   is shared across all resulting recordings. Press **Save and map fields**.
3. **Recording fields**: choose a source column **or** shared value per field.
   Editing/clicking one activates it and clears the alternative; shared values
   do not fill empty source cells in column mode. Map **Animal ID** and/or
   **Local identifier** deliberately: populated Animal ID takes precedence;
   otherwise local markings are used. Optional **Mother ID**/**Father ID** map
   parents. Duplicate headers show column letters/occurrences. Press **Save
   and resolve animals**.
4. **Animals**: confirm each existing-animal match or create a new one. Local
   markings are not unique: check the Animal ID. New animals require Protocol
   and existing Crossing. Generated temporary IDs additionally require birth
   date, breeding pair and local identifier. Expand **Overrides** for individual
   details; **Save and refresh IDs** updates previews. Parent IDs can resolve
   to exact matches or remain external text. Press **Save and map genetics**.
5. **Crossings & genotypes**: map source strains to existing crossings. Suggestions
   use names, aliases or close spelling; confirm ambiguous matches. **Save
   mappings and refresh genotype fields** updates the next controls. Choose each
   line's source column or shared result → **Save genotype columns**. **Resolve
   unknown terms** maps unfamiliar labels once → **Save term definitions**.
   Individual genotype overrides are available. Results update current animal
   calls, not recording history snapshots. Press **Save and add procedures**.
6. **Add procedures**: optionally build entries with **Add procedure**. Protocols
   must allow them. Each entry creates one procedure per resolved animal even
   when it appears on several rows. Press **Save and review import**.
7. **Review & import**: inspect **Animals and proposed changes**, **Recordings
   to create**, procedures and errors. Existing-animal changes start unchecked;
   tick only approved changes. Unchecked changes preserve values. If data or
   configuration changed, **Refresh review**. Correct errors, then **Import N
   recordings** to commit.

Combined recordings group by date and crossing. Other populated recording
fields must agree after conversion. Fix source conflicts or revise mapping/
grouping; the importer will not guess. Genotyping cannot combine crossings.
Keep Excel identifiers as text to preserve zeros; lost source zeros cannot be
recovered.

At **Recording fields**, **Template** loads column associations for that type.
**Save as template** → name → **Save template** stores column associations, not
shared values, animal choices or approvals. **Manage templates** opens
**Settings → Import templates** for rename/delete. Review mappings for each file.

No animals/procedures/recordings are created before final Import. New records
and approved updates commit together; a failure rolls back that batch. Reopening
the same completed draft does not re-import it. A separate import does not
automatically detect duplicate existing recordings. Keep the original workbook;
it is not retained as a raw file attachment. **Start another import** starts fresh.

Sources: lab/templates/lab/import_recordings.html; lab/views/spreadsheet_import.py;
docs/spreadsheet-import.md; docs/animal-parentage.md.

## reports
### Animal reports, recording exports and Home Office returns

Animal summary → **Generate report** → **Report format** (**PDF** or **Excel
(.xlsx)**) → **Download report** exports that selection. Supported selection:
1–500 animals. **Back to animal summary** returns. Reports are snapshots;
share links open current records.

Experiment Recordings → optionally select rows → **Export recordings**.
**Recordings to export** offers **Entire experiment** or **Selected recordings
only**. Choose PDF/Excel → **Download report**. With no selection, initial scope
is Entire experiment, which is not restricted to the filtered page. Select rows
and use selected scope for a subset. Exports include dates, animals, configured
metadata, paths and notes. **Back to recordings** returns.

[Reports](/reports/) → **Home Office Return** → choose **Project licence**,
**Start date**, **End date**, **Output mode** (Table/Text) → **Generate report**.
The form accepts day/month/year and ISO dates; the range must be in order.
Animal selection uses **protocol start date**, not birth date or simply the
dates of individual procedures. Only protocols permitting regulated procedures
are included. Read the generated warning and review source records before
using the result for a submission.

Sources: lab/views/animal_reports.py; lab/views/recording_reports.py;
lab/templates/lab/animal_report.html; lab/templates/lab/recording_report.html;
lab/forms/reporting.py; lab/views/reports.py; lab/templates/lab/reports_page.html.

## sharing
### Share records and enter a code

Animal summary → **Share animal**/**Share animals**, or recording row →
**Share**, opens a dialog with a code/link. Use **Copy code** or **Copy link**.
Codes start `m-` for animal selections or `r-` for recordings, then two/three
words. Sign in and press **Enter code** beside the username to open one.
Enter the code and press **Open**; case, spaces and omitted hyphens are accepted.
Direct share links also require login and grant no extra permissions.

Codes open current records. Re-sharing the same recording reuses its code;
the same animal selection and summary tab does too. Renaming animals preserves
their share identities. Partially deleted selections open surviving animals
with a notice. Entirely deleted selections/recordings are unavailable. A new
animal reusing an old ID does not inherit its deleted predecessor's codes.

Sources: docs/sharing.md; lab/templates/lab/components/share_dialogs.html;
lab/templates/lab/open_share_code.html; lab/views/sharing.py.

## settings
### Workspace settings and staff configuration

[Settings](/settings/) gives signed-in users **Import templates** and
**Maintenance**. Staff also see **Genotype categories**/**Recording types**
and the header's **Admin** link. Help documentation does not grant permissions.

- **Import templates**: find a template, **Rename** or **Delete**, then complete
  that form/confirmation. Create them in imports via **Save as template**.
  These store column associations, not upload-specific choices or approvals.
- **Maintenance**: **Find matching parents** scans without changes. Review,
  uncheck unwanted matches → **Link selected parents** to save links. Available
  to all signed-in users; see parentage for scope/expiry.
- **Genotype categories** (staff): **Add genotype category** or **Edit**. Set
  Label, Code, Position, Active → **Save changes**. Existing codes are locked.
  Uncheck Active to retire a category while retaining historical results.
- **Recording types** (staff): **Add recording type**, enter Name, Plural name,
  Slug, Description, Active → **Save changes**. **Edit type and fields** opens
  an existing type. **Add recording field** configures Key, Label, Data type,
  Required, Choices, Units, Default value, Active, Display in table, Filterable
  and Order → **Save changes**. Choices use a JSON list such as `["Low", "High"]`.
  Integer, float, text, date, choice and Yes/No types render appropriate controls.
  Required affects validation; Display in table affects visibility; Filterable
  affects available filters. Deactivating fields keeps historical values while
  removing them from new forms. Built-in **Hide recording paths column** and
  **Hide notes column** only hide table columns; values remain in add/edit forms.

If an action is absent, consider staff permissions, recording ownership, or
type/protocol availability. Ask an administrator for configuration changes;
the current AI tools cannot alter permissions or create catalogue entries.

Sources: lab/views/settings.py; lab/forms/settings.py;
lab/templates/lab/settings_form.html; lab/templates/lab/import_templates.html;
lab/templates/lab/maintenance.html; lab/templates/lab/recording_types.html.


## flare_ai_guide
### How FLARE-AI works
FLARE-AI is the AI-powered assistant, which uses local LLMs to inspect the data 
and find relevant recordings or animals. It can also provide help in using the website.
The AI is hosted in the lab's own computer GPUs. This setup processes AI requests on lab hardware 
instead of sending them to an external AI provider, although it can connect to internet-based
LLMs thorugh its settings by using the OpenAI API. 

Only members of the administrator-configured AI Django group can see or use the
assistant, including staff and superusers. Opening an empty chat or resetting it
warms the model with a hidden greeting, then displays starter questions. FLARE's
usage events record at most one metadata-only AI usage entry per user per day,
regardless of how many explicit prompts they send. They do not store prompt
text, responses, page context or conversation IDs, and the hidden greeting is
not counted.

The assistant can prepare a temporary, session-owned Add animals draft after it
has an unambiguous protocol, active crossing and Animal ID definition. It lists
missing required information separately from optional unknowns. The review link
expires after one hour and opens the existing Add animals form. No animal is
created until the user reviews the values and presses **Save**. Unsaved form
changes are not sent back to the assistant.

### Attach a file to a question
Next to the message box there is an **Attach** button. Use it to add one file to
your question. FLARE-AI accepts CSV, XLSX, TXT and Markdown files of up to
10 MB. After you choose a file, a chip shows its name and size, and a **Remove**
button clears it.

The file lives only in your open browser tab. It is sent together with the next
question you send, and it is automatically resent with follow-up questions while
the drawer stays open, so you do not need to attach it again. Closing the
drawer, choosing **Remove**, navigating to another page, or reloading the page
discards the file; reopening the drawer requires you to select it again.

FLARE-AI does not retain the file after the request or create a database record
for it. While the request is being handled, Django may hold the upload in memory
or in a temporary server file; that temporary data is released when the request
ends. In this release the assistant only receives the file's name, size, type
and extension. It cannot yet read the file's contents, so ask it to explain what
it can do with the attached file before relying on a specific answer.
