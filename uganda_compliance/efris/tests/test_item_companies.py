from unittest.mock import patch

import frappe

from uganda_compliance.efris.api_classes import e_goods_services, stock_in
from uganda_compliance.efris.tests.efris_test_utils import ensure_settings
from uganda_compliance.efris.tests.test_efris_queue import EfrisTestCase


class TestItemCompanies(EfrisTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = "EFRIS Test Hotel B"
        if not frappe.db.exists("Company", cls.company_b):
            frappe.get_doc({
                "doctype": "Company", "company_name": cls.company_b, "abbr": "ETHB",
                "default_currency": "UGX", "country": "Uganda",
                "create_chart_of_accounts_based_on": "Standard Template", "chart_of_accounts": "Standard",
            }).insert(ignore_permissions=True)
        settings = ensure_settings(cls.company_b)
        frappe.db.set_value(settings.doctype, settings.name, "tin", "1000000002")

    def setUp(self):
        super().setUp()
        settings = ensure_settings(self.company_b)
        frappe.db.set_value(settings.doctype, settings.name, "tin", "1000000002")
        frappe.local.efris_company_settings_cache = {}
        if frappe.get_meta("Item").has_field("efris_companies"):
            frappe.db.delete("EFRIS Item Company", {"parent": "EFRIS-TEST-SUGAR-1KG"})

    def item(self):
        self.assertTrue(frappe.get_meta("Item").has_field("efris_companies"), "Item needs a company list")
        item = frappe.get_doc("Item", "EFRIS-TEST-SUGAR-1KG")
        item.efris_e_company = None
        item.efris_currency = "UGX"
        item.standard_rate = 5900
        item.set("efris_companies", [{"company": self.company}, {"company": self.company_b}])
        return item

    def capture_jobs(self, jobs):
        return patch.object(frappe, "enqueue", lambda method, **kw: jobs.append(frappe._dict(method=method, **kw)))

    def test_save_queues_one_job_per_enabled_company_without_http(self):
        # No new schema needed to expose the old hook's single-company fanout.
        item = frappe.get_doc("Item", "EFRIS-TEST-SUGAR-1KG")
        item.efris_e_company = self.company
        item.efris_companies = [frappe._dict(company=c, registered=0) for c in (self.company, self.company_b, self.company_b)]
        jobs = []
        with self.capture_jobs(jobs), patch.object(e_goods_services, "make_post", side_effect=AssertionError("HTTP on save")):
            e_goods_services.before_save_item(item, "before_save")
        self.assertEqual({j.get("company") for j in jobs}, {self.company, self.company_b})
        self.assertEqual(len(jobs), 2)
        self.assertEqual(len({j.job_id for j in jobs}), 2)
        self.assertTrue(all(j.deduplicate and j.enqueue_after_commit for j in jobs))

    def test_disabled_company_is_not_queued(self):
        item = self.item()
        frappe.db.set_value("E Invoicing Settings", {"company": self.company_b}, "enabled", 0)
        frappe.local.efris_company_settings_cache = {}
        jobs = []
        with self.capture_jobs(jobs):
            item.save(ignore_permissions=True)
        self.assertEqual([j.company for j in jobs], [self.company])

    def test_workers_register_each_company_and_do_not_resend_success(self):
        item = self.item()
        jobs = []
        with self.capture_jobs(jobs):
            item.save(ignore_permissions=True)
        with patch.object(e_goods_services, "make_post", side_effect=[(False, "Not found"), (True, {})] * 2) as http:
            for job in jobs:
                frappe.get_attr(job.method)(job.name, company=job.company)
            calls = [call.kwargs for call in http.call_args_list]
            self.assertEqual([c["company_name"] for c in calls], [self.company] * 2 + [self.company_b] * 2)
            self.assertEqual([c["content"]["tin"] for c in calls if c["interfaceCode"] == "T144"], ["1000000001", "1000000002"])
            self.assertEqual({r.company: r.registered for r in item.reload().efris_companies}, {self.company: 1, self.company_b: 1})
            http.reset_mock()
            for job in jobs:
                frappe.get_attr(job.method)(job.name, company=job.company)
            http.assert_not_called()

    def test_hourly_retries_only_failed_company_even_if_legacy_is_registered(self):
        item = self.item()
        item.efris_e_company = self.company
        jobs = []
        with self.capture_jobs(jobs):
            item.save(ignore_permissions=True)
            with patch.object(e_goods_services, "make_post", side_effect=[(False, "Not found"), (True, {})]):
                e_goods_services.process_item_registration(item.name, company=self.company)
            with patch.object(e_goods_services, "make_post", side_effect=TimeoutError("URA unreachable")), self.assertRaises(TimeoutError):
                e_goods_services.process_item_registration(item.name, company=self.company_b)
            self.assertEqual(frappe.db.get_value("Item", item.name, "efris_registered"), 1)
            jobs.clear()
            stock_in.process_pending_efris_stock_entries()
        self.assertEqual([(j.name, j.company) for j in jobs if j.name == item.name], [(item.name, self.company_b)])

    def test_purchase_receipt_company_b_is_accepted_and_unlisted_company_refused(self):
        item = frappe.copy_doc(self.item())
        item.item_code = "EFRIS-E1-SHARED-STOCK"
        item.is_stock_item = 1
        with self.capture_jobs([]):
            item.insert(ignore_permissions=True)
        warehouse = frappe.db.get_value("Warehouse", {"company": self.company_b, "is_group": 0})
        frappe.db.set_value("Warehouse", warehouse, "efris_warehouse", 1)
        supplier = frappe.get_doc({
            "doctype": "Supplier", "supplier_name": "EFRIS E1 Lab Supplier",
            "supplier_group": "All Supplier Groups", "supplier_type": "Company", "tax_id": "1000000003",
        }).insert(ignore_permissions=True)
        receipt = frappe.get_doc({
            "doctype": "Purchase Receipt", "company": self.company_b, "supplier": supplier.name,
            "efris_stockin_type": "102:Local Purchase", "currency": "UGX",
            "items": [{"item_code": item.name, "qty": 2, "rate": 1000, "warehouse": warehouse,
                       "efris_receipt": 1, "efris_unit_price": 1000, "efris_currency": "UGX"}],
        })
        with self.capture_jobs([]), patch.object(stock_in, "make_post", side_effect=AssertionError("HTTP on submit")):
            receipt.insert(ignore_permissions=True)
            receipt.submit()
        self.assertEqual(frappe.db.get_value("Purchase Receipt", receipt.name, "docstatus"), 1)
        frappe.db.delete("EFRIS Item Company", {"parent": item.name, "company": self.company_b})
        with self.assertRaises(frappe.ValidationError):
            stock_in.before_save_on_purchase_receipt(receipt, "before_save")

    def test_migration_preserves_legacy_registration_and_is_idempotent(self):
        self.assertTrue(frappe.get_meta("Item").has_field("efris_companies"), "Item needs a company list")
        item = frappe.get_doc("Item", "EFRIS-TEST-SUGAR-1KG")
        frappe.db.set_value("Item", item.name, {"efris_e_company": self.company, "efris_registered": 1})
        migrate = frappe.get_attr("uganda_compliance.patches.v16.item_companies.execute")
        migrate()
        migrate()
        self.assertEqual([(r.company, r.registered) for r in item.reload().efris_companies], [(self.company, 1)])
