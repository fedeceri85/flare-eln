from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from lab.models import Mouse, Procedure, ProcedureType, Protocol


class Command(BaseCommand):
    help = "Import mice and procedures from an Excel spreadsheet."

    # ==========================================================
    # EDIT THESE SETTINGS
    # ==========================================================

    EXCEL_FILE = Path(
      "./ExampleTables/Home Office Return Jan-Dec 2026.xlsx"
    )

    SHEET_NAME = "Protocol 4"

    # This refers to protocol_number in your Django Protocol model.
    PROTOCOL_NUMBER = 4

    # If the first row contains headings and the second row is blank,
    # use 0 here. Pandas will automatically ignore fully blank rows.
    HEADER_ROW = 0

    # Excel column names
    COL_LAB_MEMBER = "Lab member initials"
    COL_LAB_BOOK_PAGE = "Lab Book page"
    COL_MOUSE_ID = "Mouse ID or Tattoo #"
    COL_DOB = "DOB"
    COL_SINGLE_MUTATION = "Single mutation"
    COL_WT = "WT"
    COL_CROSSING = "Crossing (double-triple)"
    COL_PROTOCOL_START = "Date protocol"
    COL_CULL_DATE = "Date mouse culled"
    COL_NOTES = "Notes"

    # Excel procedure column -> Django ProcedureType name
    PROCEDURE_COLUMNS = {
        "Tattooing (for genotyping)": "Tattooing (for genotyping)",
        "DOX": "DOX",
        "Tamoxifen": "Tamoxifen",
        "CNO": "CNO",
        "Surgery AAVs": "Surgery AAVs",
        "ABRs DPOAEs": "ABRs DPOAEs",
        "Noise Exposure": "Noise Exposure",
        "Ageing >15months": "Ageing >15 months",
        "EP": "EP",
        "Surgery in vivo cochlear imaging":
            "Surgery in vivo cochlear imaging",
        "Brain Surgery / imaging": "Brain Surgery / imaging",
        "Mouse Behavioural experiment":
            "Mouse Behavioural experiment",
    }

    # ==========================================================
    # IMPORT
    # ==========================================================

    def add_arguments(self, parser):
        parser.add_argument(
            "--excel-file",
            type=Path,
            default=self.EXCEL_FILE,
            help="Path to the Excel workbook to import.",
        )
        parser.add_argument(
            "--sheet-name",
            default=self.SHEET_NAME,
            help="Worksheet name to import.",
        )
        parser.add_argument(
            "--protocol-number",
            type=int,
            default=self.PROTOCOL_NUMBER,
            help="Protocol number to attach imported mice to.",
        )
        parser.add_argument(
            "--header-row",
            type=int,
            default=self.HEADER_ROW,
            help="Zero-based Excel row containing column headings.",
        )

    def handle(self, *args, **options):
        excel_file = Path(options["excel_file"]).expanduser()
        sheet_name = options["sheet_name"]
        protocol_number = options["protocol_number"]
        header_row = options["header_row"]

        if not excel_file.exists():
            raise CommandError(
                f"Excel file not found:\n{excel_file}"
            )

        try:
            protocol = Protocol.objects.get(
                protocol_number=protocol_number
            )
        except Protocol.DoesNotExist as exc:
            raise CommandError(
                f"No protocol with protocol_number="
                f"{protocol_number} exists.\n"
                "Create it in the Django admin first."
            ) from exc
        except Protocol.MultipleObjectsReturned as exc:
            raise CommandError(
                f"More than one protocol has protocol_number="
                f"{protocol_number}."
            ) from exc

        try:
            df = pd.read_excel(
                excel_file,
                sheet_name=sheet_name,
                header=header_row,
            )
            df.drop(0, axis=0, inplace=True)
            df.reset_index(drop=True, inplace=True)

        except ValueError as exc:
            raise CommandError(
                f"Could not open worksheet '{sheet_name}'."
            ) from exc

        # Remove extra spaces/newlines from Excel headings.
        df.columns = [
            self.normalise_column_name(column)
            for column in df.columns
        ]

        self.validate_columns(df)

        created_mice = 0
        updated_mice = 0
        created_procedures = 0
        skipped_rows = 0

        with transaction.atomic():
            for excel_index, row in df.iterrows():
                excel_row_number = excel_index + header_row + 2

                tattoo_id = self.clean_identifier(
                    row[self.COL_MOUSE_ID]
                )
                source_mouse_id = self.source_mouse_id(tattoo_id)
                lab_book_page = self.clean_identifier(
                    row[self.COL_LAB_BOOK_PAGE]
                )


                if not tattoo_id:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipping Excel row {excel_row_number}: "
                            "mouse/local identifier is empty."
                        )
                    )
                    skipped_rows += 1
                    continue


                #Determine whether WT, single mutation or multiple mutations:
                if not pd.isna(row[self.COL_WT]):
                    genotype = "WT"
                    crossing = "WT"
                    crossing_type = "WT"
                elif not pd.isna(row[self.COL_SINGLE_MUTATION]):
                    crossing = self.clean_text(row[self.COL_SINGLE_MUTATION])
                    genotype = ""
                    crossing_type = "Single"
                elif not pd.isna(row[self.COL_CROSSING]):
                    crossing = self.clean_text(row[self.COL_CROSSING])
                    genotype = ""
                    crossing_type = "Multiple"

                cull_raw = row[self.COL_CULL_DATE]
                cull_date = self.parse_date(cull_raw)

                custom_metadata = {
                    "original_tattoo_id": tattoo_id,
                    "original_cull_entry": self.clean_text(cull_raw),
                    "notes": self.clean_text(
                        row.get(self.COL_NOTES)
                    ),
                }

                try:
                    dob = self.parse_date(row[self.COL_DOB])
                except:
                    dob = None

                #Determine or generate mouse ID.
                if len(str(source_mouse_id)) > 4:
                    #Mouse has a real id
                    mouse_id = str(source_mouse_id)
                else:
                    #Mouse was a pup
                    mouse_id = str(dob) + "-" + crossing + "-" + str(tattoo_id) + "-" + str(protocol_number) + "-" + str(self.clean_text(row[self.COL_LAB_MEMBER])) + "-" + str(excel_row_number)


                defaults = {
                    "date_of_birth": dob,
                    # "genotype": self.clean_text(
                    #     row[self.COL_SINGLE_MUTATION]
                    #     or row.get(self.COL_WT)
                    # ),
                    "genotype": genotype,
                    "crossing": crossing,
                    "crossing_type": crossing_type,
                    "status": "Culled" if cull_date else "Alive",
                    "protocol": protocol,
                    "protocol_start_date": self.parse_date(
                        row[self.COL_PROTOCOL_START]
                    ),
                    "cull_date": cull_date,
                    "custom_metadata": custom_metadata,
                }

                mouse, created = Mouse.objects.update_or_create(
                    mouse_id=mouse_id,
                    defaults=defaults,
                )

                if created:
                    created_mice += 1
                else:
                    updated_mice += 1

                for excel_column, procedure_name in (
                    self.PROCEDURE_COLUMNS.items()
                ):
                    if excel_column not in df.columns:
                        continue

                    if not self.is_marked(row[excel_column]):
                        continue

                    procedure_type, _ = (
                        ProcedureType.objects.get_or_create(
                            name=procedure_name
                        )
                    )

                    procedure_values = {
                        "mouse": mouse,
                        "procedure_type": procedure_type,
                        "lab_member": self.clean_text(row[self.COL_LAB_MEMBER]),
                        "lab_book_page": self.clean_text(
                            row[self.COL_LAB_BOOK_PAGE]
                        ),
                    }
                    procedure_created = False

                    if not Procedure.objects.filter(**procedure_values).exists():
                        Procedure.objects.create(**procedure_values)
                        procedure_created = True

                    if procedure_created:
                        created_procedures += 1

        self.stdout.write(
            self.style.SUCCESS(
                "\nImport complete\n"
                f"New mice: {created_mice}\n"
                f"Updated mice: {updated_mice}\n"
                f"New procedures: {created_procedures}\n"
                f"Skipped rows: {skipped_rows}"
            )
        )

    # ==========================================================
    # HELPERS
    # ==========================================================

    def validate_columns(self, df):
        required_columns = [
            self.COL_LAB_MEMBER,
            self.COL_LAB_BOOK_PAGE,
            self.COL_MOUSE_ID,
            #self.COL_DOB,
            self.COL_SINGLE_MUTATION,
            self.COL_WT,
            self.COL_CROSSING,
            self.COL_PROTOCOL_START,
            self.COL_CULL_DATE,
        ]

        missing = [
            column
            for column in required_columns
            if column not in df.columns
        ]

        if missing:
            available = "\n".join(
                f"- {column}" for column in df.columns
            )
            missing_text = "\n".join(
                f"- {column}" for column in missing
            )

            raise CommandError(
                "The following required columns are missing:\n"
                f"{missing_text}\n\n"
                "Columns found in the spreadsheet:\n"
                f"{available}"
            )

    @staticmethod
    def normalise_column_name(value):
        return " ".join(
            str(value).replace("\n", " ").split()
        )

    @staticmethod
    def clean_identifier(value):
        if pd.isna(value):
            return ""

        if isinstance(value, float) and value.is_integer():
            return str(int(value))

        return str(value).strip()

    @staticmethod
    def clean_text(value):
        if value is None or pd.isna(value):
            return ""

        if isinstance(value, float) and value.is_integer():
            return str(int(value))

        return str(value).strip()

    @staticmethod
    def source_mouse_id(value):
        parts = [
            part.strip()
            for part in str(value).split("/")
            if part.strip()
        ]

        if len(parts) > 1:
            return parts[-1]

        return value

    @staticmethod
    def parse_date(value):
        if value is None or pd.isna(value):
            return None

        # Real Excel dates
        parsed = pd.to_datetime(
            value,
            dayfirst=True,
            errors="coerce",
        )

        if not pd.isna(parsed):
            return parsed.date()

        # Cells may contain entries such as "13/1/26 RM"
        first_part = str(value).strip().split()[0]

        parsed = pd.to_datetime(
            first_part,
            dayfirst=True,
            errors="coerce",
        )

        if pd.isna(parsed):
            return None

        return parsed.date()

    @staticmethod
    def is_marked(value):
        if value is None or pd.isna(value):
            return False

        if isinstance(value, str):
            value = value.strip().lower()
            return value not in {
                "",
                "0",
                "no",
                "n",
                "false",
            }

        return bool(value)

    @staticmethod
    def build_mouse_id(lab_book_page, tattoo_id):
        # Tattoo numbers can repeat between lab-book pages.
        # Example: page 106, tattoo 1 -> "106-1"
        if lab_book_page:
            return f"{lab_book_page}-{tattoo_id}"

        return tattoo_id
