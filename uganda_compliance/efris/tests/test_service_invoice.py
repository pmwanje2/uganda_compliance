import frappe

from uganda_compliance.efris.api_classes import e_invoice
from uganda_compliance.efris.tests.efris_test_utils import (
    TEST_CUSTOMER,
    TEST_ITEM,
    fake_ura,
    run_queued,
)
from uganda_compliance.efris.tests.test_efris_queue import EfrisTestCase


class TestServiceInvoice(EfrisTestCase):
    def room_night_invoice(self):
        item = frappe.copy_doc(frappe.get_doc("Item", TEST_ITEM))
        item.item_code = "EFRIS-TEST-ROOM-NIGHT"
        item.item_name = "Room night (non-stock service)"
        item.efris_item = 0
        item.is_stock_item = 0
        if not frappe.db.exists("Item", item.item_code):
            item.insert(ignore_permissions=True)
        frappe.db.set_value("Item", item.name, "efris_item", 1)
        frappe.db.set_value("Item", item.name, "is_stock_item", 0)
        return frappe.get_doc({
            "doctype": "Sales Invoice", "company": self.company, "customer": TEST_CUSTOMER,
            "currency": "UGX", "conversion_rate": 1, "update_stock": 0,
            "taxes_and_charges": self.ctx.sales_taxes_template,
            "items": [{"item_code": "EFRIS-TEST-ROOM-NIGHT", "qty": 1, "rate": 118000, "uom": "Nos"}],
        })

    def test_room_night_with_default_non_efris_warehouse_is_fiscalised(self):
        invoice = self.room_night_invoice()
        with fake_ura() as ura:
            invoice.insert(ignore_permissions=True)
            warehouse = invoice.items[0].warehouse
            if warehouse:
                self.assertFalse(frappe.db.get_value("Warehouse", warehouse, "efris_warehouse"))
            invoice.reload()
            invoice.save(ignore_permissions=True)
            invoice.submit()
            self.assertEqual(invoice.docstatus, 1)
            self.assertEqual(invoice.efris_invoice, 1)
            self.assertEqual(run_queued(ura)[0]["outcome"], "submitted")
        self.assertTrue(frappe.db.get_value("Sales Invoice", invoice.name, "efris_irn"))

    def test_stock_item_without_warehouse_still_refused(self):
        frappe.db.set_value("Item", TEST_ITEM, "is_stock_item", 1)
        invoice = frappe.get_doc({
            "doctype": "Sales Invoice", "company": self.company, "customer": TEST_CUSTOMER,
            "items": [{"item_code": TEST_ITEM}],
        })
        with self.assertRaises(frappe.ValidationError):
            e_invoice.set_efris_based_on_items(invoice, invoice.items)

    def test_saved_efris_stock_line_in_non_efris_warehouse_still_refused(self):
        frappe.db.set_value("Item", TEST_ITEM, "is_stock_item", 1)
        warehouse = frappe.db.get_value("Warehouse", {"company": self.company, "is_group": 0, "efris_warehouse": 0})
        invoice = frappe.get_doc({
            "doctype": "Sales Invoice", "company": self.company, "customer": TEST_CUSTOMER, "efris_invoice": 1,
            "items": [{"item_code": TEST_ITEM, "warehouse": warehouse}],
        })
        with self.assertRaises(frappe.ValidationError):
            e_invoice.validate_efris_warehouse(invoice)
