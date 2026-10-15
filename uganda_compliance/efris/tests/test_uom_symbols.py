"""URA package-measure units keep their fiscal code and a human symbol.

Uganda Revenue Authority maintains the EFRIS package measure unit list and
asks a taxpayer to request a unit that is missing from it:
https://ura.go.ug/en/efris/stock-management/
This app already ships that list as UOM fixtures and sends ``efris_uom_code``
as ``unitOfMeasure``. When the UOM name is ``<code>-<symbol>`` and the prefix
is that fiscal code, the human symbol is the remainder. Installing the app
must set it, so a Uganda tenant does not repeat the same symbols.
"""

import json
import unittest
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "uom.json"

# ERPNext's own unit names, mapped onto URA codes. Names stay as they are.
STANDARD_URA_CODES = {
    "g": "109",
    "Kg": "103",
    "Litre": "102",
    "Nos": "NMB",
}


def _rows():
    return json.loads(FIXTURE.read_text())


def _coded_symbols(rows):
    found = []
    for row in rows:
        code = row.get("efris_uom_code") or ""
        prefix, separator, symbol = row["name"].partition("-")
        if separator and prefix == code and symbol:
            found.append((row, symbol))
    return found


class TestUomSymbols(unittest.TestCase):
    def test_coded_ura_units_carry_human_symbols(self):
        rows = _rows()
        by_name = {row["name"]: row for row in rows}
        coded = _coded_symbols(rows)
        problems = []
        if len(coded) != 434:
            problems.append(f"coded units {len(coded)} != 434")
        missing = [row["name"] for row, symbol in coded if row.get("symbol") != symbol]
        if missing:
            problems.append(f"missing symbols {len(missing)}: {missing[:8]}")
        coded_names = {row["name"] for row, _symbol in coded}
        extras = sorted(row["name"] for row in rows if row.get("symbol") and row["name"] not in coded_names)
        if extras:
            problems.append(f"unexpected symbols {extras[:8]}")
        for name, code in STANDARD_URA_CODES.items():
            row = by_name[name]
            if row.get("efris_uom_code") != code or row.get("uom_name") != name:
                problems.append(
                    f"standard {name}: code {row.get('efris_uom_code')!r} name {row.get('uom_name')!r}"
                )
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
