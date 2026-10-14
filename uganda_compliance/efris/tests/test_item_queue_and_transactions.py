from unittest.mock import patch

import frappe

from uganda_compliance.efris.api_classes import e_goods_services, e_invoice, stock_in
from uganda_compliance.efris.page.efris_synchronizatio import efris_synchronization_center
from uganda_compliance.efris.tests.efris_test_utils import fake_ura, make_pos_invoice
from uganda_compliance.efris.tests.test_efris_queue import EfrisTestCase


class TestItemQueueAndTransactions(EfrisTestCase):
    def test_item_save_queues_registration_without_contacting_ura(self):
        item = frappe.get_doc("Item", "EFRIS-TEST-SUGAR-1KG")
        item.efris_e_company = self.company
        item.standard_rate = 5900
        item.efris_currency = "UGX"
        jobs = []
        with patch.object(e_goods_services, "make_post", side_effect=TimeoutError("URA unreachable")) as http, patch.object(
            frappe, "enqueue", lambda method, **kw: jobs.append(frappe._dict(method=method, **kw))
        ):
            item.save(ignore_permissions=True)
            self.assertTrue(frappe.db.exists("Item", item.name))
            http.assert_not_called()
            self.assertEqual(len(jobs), 1)
            self.assertTrue(jobs[0].enqueue_after_commit)
            self.assertTrue(jobs[0].deduplicate)
            with patch.object(e_goods_services, "make_post", side_effect=[(False, "No goods found"), (True, {})]) as worker_http:
                frappe.get_attr(jobs[0].method)(jobs[0].name)
            self.assertEqual([call.kwargs["interfaceCode"] for call in worker_http.call_args_list], ["T144", "T130"])
        self.assertEqual(frappe.db.get_value("Item", item.name, "efris_registered"), 1)

    def test_registration_success_is_persisted_without_resaving_item(self):
        item = frappe.get_doc("Item", "EFRIS-TEST-SUGAR-1KG")
        frappe.db.set_value("Item", item.name, "efris_registered", 0)
        item.reload()
        with patch.object(e_goods_services, "make_post", return_value=(True, {})):
            e_goods_services.upload_item_to_efris(item, self.company, {})
        self.assertEqual(frappe.db.get_value("Item", item.name, "efris_registered"), 1)

    def test_hourly_retries_item_registration_after_outage(self):
        item = frappe.get_doc("Item", "EFRIS-TEST-SUGAR-1KG")
        item.efris_e_company = self.company
        item.standard_rate = 5900
        item.efris_currency = "UGX"
        jobs = []
        with patch.object(frappe, "enqueue", lambda method, **kw: jobs.append(frappe._dict(method=method, **kw))):
            item.save(ignore_permissions=True)
            with patch.object(e_goods_services, "make_post", side_effect=TimeoutError("URA unreachable")):
                with self.assertRaises(TimeoutError):
                    frappe.get_attr(jobs[0].method)(jobs[0].name)
            self.assertEqual(frappe.db.get_value("Item", item.name, "efris_registered"), 0)
            jobs.clear()
            stock_in.process_pending_efris_stock_entries()
            self.assertTrue(any(job.name == item.name for job in jobs))
            jobs.clear()
            frappe.db.set_value("E Invoicing Settings", {"company": self.company}, "enabled", 0)
            frappe.local.efris_company_settings_cache = {}
            stock_in.process_pending_efris_stock_entries()
            self.assertFalse(any(job.name == item.name for job in jobs))

    def test_discount_hook_does_not_commit_enclosing_transaction(self):
        with fake_ura():
            invoice = make_pos_invoice(self.ctx)
        row = invoice.items[0]
        with patch.object(frappe.db, "commit") as commit:
            e_invoice._update_row_values(row, -590, -90, 18, 900, "Test discount", False)
        commit.assert_not_called()
        self.assertEqual(frappe.db.get_value(row.doctype, row.name, "efris_dsct_discount_total"), -590)

    def test_hourly_sync_center_does_not_require_a_company_argument(self):
        # The stock sweep already gates each company's enabled settings; the old
        # no-argument get_e_company_settings call crashed before reaching it.
        with patch.object(efris_synchronization_center, "process_pending_efris_stock_entries", return_value=[]) as sweep:
            efris_synchronization_center.process_pending_efris_entries()
        sweep.assert_called_once_with()

