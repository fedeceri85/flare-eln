from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from ..management.commands.import_mice import Command as ImportMiceCommand
from ..models import Protocol


class ImportMiceCommandTests(SimpleTestCase):
    def test_command_line_options_override_defaults(self):
        command = ImportMiceCommand()
        parser = command.create_parser("manage.py", "import_mice")
        options = parser.parse_args(
            [
                "--excel-file",
                "/tmp/mice.xlsx",
                "--sheet-name",
                "Protocol 5",
                "--protocol-number",
                "5",
                "--header-row",
                "2",
            ]
        )

        self.assertEqual(options.excel_file, Path("/tmp/mice.xlsx"))
        self.assertEqual(options.sheet_name, "Protocol 5")
        self.assertEqual(options.protocol_number, 5)
        self.assertEqual(options.header_row, 2)

    def test_handle_uses_protocol_number_option(self):
        command = ImportMiceCommand()

        with TemporaryDirectory() as temporary_directory:
            excel_file = Path(temporary_directory) / "mice.xlsx"
            excel_file.touch()

            with patch(
                "lab.management.commands.import_mice.Protocol.objects.get",
                side_effect=Protocol.DoesNotExist,
            ) as get_protocol:
                with self.assertRaisesMessage(
                    CommandError,
                    "protocol_number=987654",
                ):
                    command.handle(
                        excel_file=excel_file,
                        sheet_name="Protocol 987654",
                        protocol_number=987654,
                        header_row=0,
                    )

                get_protocol.assert_called_once_with(
                    protocol_number=987654,
                )

    def test_source_mouse_id_uses_last_slash_separated_value(self):
        command = ImportMiceCommand()

        self.assertEqual(
            command.source_mouse_id("1_M_LE / 411859"),
            "411859",
        )
        self.assertEqual(
            command.source_mouse_id("411859"),
            "411859",
        )
