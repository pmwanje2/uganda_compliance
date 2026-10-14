import json
from pathlib import Path

import frappe
from frappe.utils.fixtures import sync_fixtures
from frappe.modules.utils import sync_customizations_for_doctype
from frappe.tests.utils import FrappeTestCase

APP = Path(__file__).resolve().parents[2]


class TestMigratePreservesNativeConfig(FrappeTestCase):
	def test_customer_and_company_permissions_survive_customization_sync(self):
		for doctype in ("Customer", "Company"):
			with self.subTest(doctype=doctype):
				row = frappe.get_doc({
					"doctype": "Custom DocPerm", "parent": doctype,
					"parenttype": "DocType", "parentfield": "permissions",
					"role": "Sales User", "permlevel": 0, "read": 1, "write": 1,
				}).insert(ignore_permissions=True)
				before = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields=["*"])
				folder = APP / "efris/custom"
				data = json.loads((folder / f"{doctype.lower()}.json").read_text())
				sync_customizations_for_doctype(data, str(folder))
				after = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields=["*"])
				self.assertEqual(sorted(before, key=lambda r: r.name), sorted(after, key=lambda r: r.name))
				self.assertTrue(frappe.db.exists("Custom DocPerm", row.name))

	def test_currency_fixture_preserves_ugx_number_format(self):
		frappe.db.set_value("Currency", "UGX", "number_format", "#,###")
		sync_fixtures("uganda_compliance")
		self.assertEqual(frappe.db.get_value("Currency", "UGX", "number_format"), "#,###")
