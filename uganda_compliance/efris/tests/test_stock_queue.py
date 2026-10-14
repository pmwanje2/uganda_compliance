from unittest.mock import patch

import frappe

from uganda_compliance.efris.api_classes import stock_in
from uganda_compliance.efris.tests.test_efris_queue import EfrisTestCase


class TestStockQueue(EfrisTestCase):
    def setUp(self):
        super().setUp()
        self.jobs = []
        self.enqueue = patch.object(
            frappe, "enqueue", lambda method, **kw: self.jobs.append(frappe._dict(method=method, **kw))
        )
        self.enqueue.start()
        self.addCleanup(self.enqueue.stop)
        self.item = "EFRIS-TEST-BOTTLE-QUEUE"
        if not frappe.db.exists("Item", self.item):
            frappe.get_doc({
                "doctype": "Item", "item_code": self.item, "item_name": "Test bottled drink",
                "item_group": "EFRIS Test Goods", "stock_uom": "Nos", "is_stock_item": 1,
            }).insert(ignore_permissions=True)
        frappe.db.set_value("Item", self.item, {
            "efris_item": 1, "efris_e_company": self.company, "efris_product_code": self.item,
        })
        self.supplier = "EFRIS Test Stock Supplier"
        if not frappe.db.exists("Supplier", self.supplier):
            frappe.get_doc({
                "doctype": "Supplier", "supplier_name": self.supplier,
                "supplier_group": "All Supplier Groups", "supplier_type": "Company",
                "tax_id": "1000000002",
            }).insert(ignore_permissions=True)

    def receipt(self):
        doc = frappe.get_doc({
            "doctype": "Purchase Receipt", "company": self.company,
            "supplier": self.supplier, "efris_stockin_type": "102:Local Purchase",
            "supplier_tin": "1000000002", "currency": "UGX",
            "items": [{"item_code": self.item, "qty": 10, "rate": 1000,
                       "warehouse": self.ctx.warehouse, "efris_receipt": 1,
                       "efris_unit_price": 1000, "efris_currency": "UGX"}],
        })
        doc.insert(ignore_permissions=True)
        doc.submit()
        return doc

    def worker(self, doc):
        job = next(j for j in self.jobs if j.name == doc.name)
        self.assertTrue(job.enqueue_after_commit)
        self.assertTrue(job.deduplicate)
        return frappe.get_attr(job.method)(job.doctype, job.name)

    def test_purchase_receipt_submit_does_not_call_unreachable_ura(self):
        with patch.object(stock_in, "make_post", side_effect=TimeoutError("URA unreachable")) as http:
            doc = self.receipt()
            self.assertEqual(doc.docstatus, 1)
            self.assertFalse(doc.items[0].efris_registered, "child remains pending until the worker sends T131")
            http.assert_not_called()
            self.assertTrue(any(j.name == doc.name for j in self.jobs))

    def test_worker_retries_unregistered_goods_and_does_not_resend_success(self):
        with patch.object(stock_in, "make_post", return_value=(False, "URA unreachable")):
            doc = self.receipt()
            self.worker(doc)
        self.assertFalse(frappe.get_doc(doc.doctype, doc.name).items[0].efris_registered)
        self.jobs.clear()
        stock_in.process_pending_efris_stock_entries()
        self.assertTrue(any(j.name == doc.name for j in self.jobs))
        with patch.object(stock_in, "make_post", return_value=(True, {})) as http:
            self.worker(doc)
            self.assertEqual(http.call_args.kwargs["content"]["goodsStockIn"]["operationType"], "101")
            self.assertEqual(http.call_args.kwargs["content"]["goodsStockIn"]["supplierTin"], "1000000002")
            self.assertTrue(frappe.get_doc(doc.doctype, doc.name).items[0].efris_registered)
            http.reset_mock()
            self.worker(doc)
            http.assert_not_called()

    def test_wastage_queues_damaged_adjust_out(self):
        with patch.object(stock_in, "make_post", return_value=(True, {})) as http:
            self.receipt()
            self.jobs.clear()
            doc = frappe.get_doc({
                "doctype": "Stock Entry", "company": self.company,
                "stock_entry_type": "Material Issue", "purpose": "Material Issue",
                "efris_adjustment_type": "102:Damaged/Breakage", "remarks": "Broken bottle",
                "items": [{"item_code": self.item, "qty": 1, "s_warehouse": self.ctx.warehouse,
                           "basic_rate": 1000}],
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            http.assert_not_called()
            self.assertTrue(any(j.name == doc.name for j in self.jobs))
            self.worker(doc)
            payload = http.call_args.kwargs["content"]
            self.assertEqual(payload["goodsStockIn"]["operationType"], "102")
            self.assertEqual(payload["goodsStockIn"]["adjustType"], "102")
            self.assertEqual(payload["goodsStockInItem"][0]["quantity"], "1.0")
            self.assertTrue(frappe.get_doc(doc.doctype, doc.name).items[0].efris_registered)

    def test_stock_count_shortfall_is_queued(self):
        with patch.object(stock_in, "make_post", return_value=(True, {})) as http:
            self.receipt()
            self.jobs.clear()
            doc = frappe.get_doc({
                "doctype": "Stock Reconciliation", "company": self.company,
                "purpose": "Stock Reconciliation",
                "items": [{"item_code": self.item, "warehouse": self.ctx.warehouse,
                           "qty": 9, "valuation_rate": 1000, "efris_reconcilliation": 1,
                           "efris_adjustment_type": "104:Others. (Please specify)",
                           "efris_remarks": "Count shortfall under investigation"}],
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            http.assert_not_called()
            self.worker(doc)
            self.assertEqual(http.call_args.kwargs["content"]["goodsStockIn"]["adjustType"], "104")

    def test_waste_reasons_map_to_ura_codes(self):
        for reason, code in (("Expired", "101"), ("Damaged/Breakage", "102"),
                             ("Staff meal/Comp", "103"), ("Missing/other", "104")):
            with self.subTest(reason=reason):
                with patch.object(stock_in, "make_post", return_value=(True, {})) as http:
                    doc = frappe.get_doc({
                        "doctype": "Stock Entry", "name": "WASTE-TEST", "company": self.company,
                        "purpose": "Material Issue", "efris_adjustment_type": reason,
                        "remarks": "Recorded reason", "posting_date": frappe.utils.nowdate(),
                        "items": [{"item_code": self.item, "qty": 1, "basic_rate": 1000,
                                   "s_warehouse": self.ctx.warehouse, "stock_uom": "Nos"}],
                    })
                    stock_in.send_stock_entry(doc)
                    self.assertIsNotNone(http.call_args)
                    self.assertEqual(http.call_args.kwargs["content"]["goodsStockIn"]["adjustType"], code)
