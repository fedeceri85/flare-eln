24/11/2026
- Display genotyping and crossing in all experiments, not just the genotyping ones.

22/11/2026
- AI assistant now sees the lab's active crossings in its vocabulary up front (e.g. TMC1), so short animal codes are recognised without an extra tool call; inactive crossings are excluded and the list is capped with a pointer to list_crossings.
- AI assistant now recognises short animal codes such as gene symbols (e.g. TMC1) as crossings or constituent lines and routes them to the crossing search before giving up.
- Hardened the AI assistant settings: the assistant name is now validated as a short single-line label and is presented to the model as an explicitly untrusted display name, so it can no longer be used to inject instructions into the system prompt.
- AI chat now shows which tools the assistant used for each answer as a small collapsed line that expands to the raw tool names with repeat counts.

21/11/2026
- Added vision attachment support: upload images (PNG, JPG, WEBP) and PDFs (up to 15 pages) for AI transcription of handwritten lab notes.
- Added search_procedures and search_projects tools to the AI assistant.
- Added list_crossings tool so the assistant can reference the genetic crossing catalogue when disambiguating transcriptions.
- Added read_attachment tool for reading CSV and XLSX files in chat; presents structured data with sheet selection support.


19/11/2026
- Improvements to "Add recording" interface
- General bug fixes
- Added ages to experiments (age of animal at the time of experiment.)

18/11/2026
- Added file attachment to AI assistant.
- Added "add animals" functionality to AI assistant.

16/11/2026
- First version of FLARE-AI assistant. 
- Added a conversational, review-only Add animals form draft; the existing Save action remains the only record-creation path.
- Four tools are available: search animals, search recordings, the app guide and preparation of animal form drafts.

29/10/2026
- Added shareable link creation, allowing sharing group of animals or individual recordings. 
- Shortened automatic animal ids. 
- Added reports.

27/10/2026
- Added optional external and linked mother/father IDs, family summaries, grouped offspring, and explicit matching of external parent IDs.
- Added parent matching and approvals to spreadsheet imports within the existing workflow.

25/10/2026
- Added page for simultaneous view of multiple animals
- Added the possibility to add an experiment from edit animal view.
- Added Media support.

24/10/2026
- Import experiments from spreadsheet - first version. 
- Added aliases to animal lines, to favour matching in spreadsheet imports


23/10/2026
- Improved lines and crossings page
- Visual enhancements to the procedure tables.
- Changed all references to "mice" in the interface to "animals". 

22/10/2026
- Upgraded Django from unsupported 4.2.30 to 5.2.18 LTS and updated deprecated HTML helpers.
- Visual overhaul and code cleanup.
- Unification of the add one mouse/litter and edit interfaces.

21/10/2026
- Refreshed the lab interface and login with consistent navigation, breadcrumbs, and a light theme.
- Added persistent Save and Cancel actions on long forms, clearer validation, and responsive tables.
- Preserved list filters and workflow context when opening and returning from records.
- User-determined zygozity choices through admin panel. Added "Uncertain" label to genotype result to mark not clear-cut results.
- NEW add litter form, which supports mixtures of ranges and individual ids. 
- Change the rule to check for existing animals with auto-generated IDs. 

21/10/2026
NEW: switch to Docker images.
- Feature: allow easy import of mulitple paths at once.
Solved bugs
- home office return crashes with "non recovery" status.
- date erased when procedure updated
- add positional argument to gene lists, so that they are displayed in the correct order.
- allow bulk changes in the notes.
- preserve entered information in forms if switching forms in between. 


25/08/2026
- Added entry log to the admin website.


24/08/2026
- Enforced uniqueness of DOB-BreedingPair-crossing-tattoo
- Warning if trying to add a mouse where a similar mouse with the same dob,breeding pair and crossing already exists.
- Cleaned admin page.
- Improved visualisation on procedure page by only showing procedures that are actually present in the filtered mice.

