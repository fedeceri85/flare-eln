22/10/2026
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

