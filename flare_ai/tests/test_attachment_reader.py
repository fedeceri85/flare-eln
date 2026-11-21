"""Tests for the read_attachment tool (CSV and XLSX)."""

import csv
import io
from unittest.mock import MagicMock

from django.test import TestCase

from flare_ai.attachments import RequestAttachment
from flare_ai.tools.attachment_reader import read_attachment, _cell_to_str, _read_csv, _read_xlsx


def _make_attachment(suffix: str, content: bytes, name: str = "test") -> RequestAttachment:
    upload = MagicMock()
    upload.read.return_value = content
    upload.seek = MagicMock()
    return RequestAttachment(
        identifier="file-1",
        name=f"{name}{suffix}",
        suffix=suffix,
        size=len(content),
        content_type="application/octet-stream",
        upload=upload,
    )


def _make_ctx(attachment: RequestAttachment | None) -> MagicMock:
    ctx = MagicMock()
    ctx.deps.attachment = attachment
    return ctx


class CSVReadTests(TestCase):
    def test_simple_csv(self):
        content = b"Name,Age,Species\nMouse1,12,Mouse\nMouse2,15,Mouse\n"
        att = _make_attachment(".csv", content)
        result = _read_csv(att.upload, max_rows=100)
        self.assertEqual(result["header"], ["Name", "Age", "Species"])
        self.assertEqual(result["row_count"], 2)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["rows"][0], ["Mouse1", "12", "Mouse"])

    def test_csv_truncation(self):
        rows = ["id,value"] + [f"{i},{i * 2}" for i in range(50)]
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        att = _make_attachment(".csv", buf.getvalue().encode())
        result = _read_csv(att.upload, max_rows=10)
        self.assertEqual(result["row_count"], 10)
        self.assertTrue(result["truncated"])

    def test_csv_empty_file(self):
        att = _make_attachment(".csv", b"")
        result = _read_csv(att.upload, max_rows=100)
        self.assertIn("error", result)

    def test_csv_with_bom(self):
        content = b"\xef\xbb\xbfName,Age\nMouse1,12\n"
        att = _make_attachment(".csv", content)
        result = _read_csv(att.upload, max_rows=100)
        self.assertEqual(result["header"][0], "Name")

    def test_csv_quoted_fields(self):
        content = b'Name,Notes\n"Mouse, 1","has a, comma"\n'
        att = _make_attachment(".csv", content)
        result = _read_csv(att.upload, max_rows=100)
        self.assertEqual(result["rows"][0][0], "Mouse, 1")
        self.assertEqual(result["rows"][0][1], "has a, comma")

    def test_csv_latin1_fallback(self):
        content = "Name,Sp\u00e9cie\nMouse1,Mouse\n".encode("latin-1")
        att = _make_attachment(".csv", content)
        result = _read_csv(att.upload, max_rows=100)
        self.assertEqual(result["header"][0], "Name")


class XLSXReadTests(TestCase):
    def _make_xlsx(self, sheets: dict[str, list[list]]) -> bytes:
        import openpyxl
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, rows in sheets.items():
            ws = wb.create_sheet(title=name)
            for row in rows:
                ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_single_sheet(self):
        content = self._make_xlsx({"Data": [["Name", "Age"], ["Mouse1", 12]]})
        att = _make_attachment(".xlsx", content)
        result = _read_xlsx(att.upload, max_rows=100)
        self.assertEqual(result["format"], "xlsx")
        self.assertEqual(result["sheet"], "Data")
        self.assertEqual(result["header"], ["Name", "Age"])
        self.assertEqual(result["rows"][0], ["Mouse1", "12"])
        self.assertEqual(result["row_count"], 1)

    def test_multi_sheet_lists_available(self):
        content = self._make_xlsx({
            "Alpha": [["h1"], ["a1"]],
            "Beta": [["h2"], ["b1"]],
        })
        att = _make_attachment(".xlsx", content)
        result = _read_xlsx(att.upload, max_rows=100)
        self.assertEqual(result["sheet"], "Alpha")
        self.assertEqual(result["available_sheets"], ["Alpha", "Beta"])

    def test_select_specific_sheet(self):
        content = self._make_xlsx({
            "Alpha": [["h1"], ["a1"]],
            "Beta": [["h2", "x"], ["b1", 42]],
        })
        att = _make_attachment(".xlsx", content)
        result = _read_xlsx(att.upload, sheet="Beta", max_rows=100)
        self.assertEqual(result["sheet"], "Beta")
        self.assertEqual(result["header"], ["h2", "x"])
        self.assertEqual(result["rows"][0], ["b1", "42"])

    def test_nonexistent_sheet(self):
        content = self._make_xlsx({"Alpha": [["h1"], ["a1"]]})
        att = _make_attachment(".xlsx", content)
        result = _read_xlsx(att.upload, sheet="Nope", max_rows=100)
        self.assertIn("error", result)
        self.assertEqual(result["available_sheets"], ["Alpha"])

    def test_xlsx_truncation(self):
        rows = [["id"]] + [[i] for i in range(50)]
        content = self._make_xlsx({"Data": rows})
        att = _make_attachment(".xlsx", content)
        result = _read_xlsx(att.upload, max_rows=5)
        self.assertEqual(result["row_count"], 5)
        self.assertTrue(result["truncated"])

    def test_float_cells(self):
        content = self._make_xlsx({"Data": [["val"], [3.0], [3.14]]})
        att = _make_attachment(".xlsx", content)
        result = _read_xlsx(att.upload, max_rows=100)
        self.assertEqual(result["rows"][0], ["3"])
        self.assertEqual(result["rows"][1], ["3.14"])


class ReadAttachmentDispatchTests(TestCase):
    def test_no_attachment(self):
        ctx = _make_ctx(None)
        result = read_attachment(ctx)
        self.assertIn("error", result)
        self.assertIn("No file is attached", result["error"])

    def test_image_rejected(self):
        att = _make_attachment(".png", b"fakepng")
        ctx = _make_ctx(att)
        result = read_attachment(ctx)
        self.assertIn("error", result)
        self.assertIn("not .png", result["error"])

    def test_pdf_rejected(self):
        att = _make_attachment(".pdf", b"fakepdf")
        ctx = _make_ctx(att)
        result = read_attachment(ctx)
        self.assertIn("error", result)
        self.assertIn("not .pdf", result["error"])


class CellToStringTests(TestCase):
    def test_none(self):
        self.assertEqual(_cell_to_str(None), "")

    def test_int_float(self):
        self.assertEqual(_cell_to_str(42.0), "42")

    def test_decimal_float(self):
        self.assertEqual(_cell_to_str(3.14), "3.14")

    def test_string(self):
        self.assertEqual(_cell_to_str("hello"), "hello")

    def test_int(self):
        self.assertEqual(_cell_to_str(7), "7")