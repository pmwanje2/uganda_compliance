import frappe


def execute():
    frappe.reload_doc("efris", "doctype", "efris_item_company")
    for item in frappe.get_all("Item", filters={"efris_e_company": ["is", "set"]},
                               fields=["name", "efris_e_company", "efris_registered"]):
        if frappe.db.exists("EFRIS Item Company", {"parent": item.name, "company": item.efris_e_company}):
            continue
        frappe.get_doc({
            "doctype": "EFRIS Item Company", "parent": item.name, "parenttype": "Item",
            "parentfield": "efris_companies", "company": item.efris_e_company,
            "registered": item.efris_registered,
            "idx": frappe.db.count("EFRIS Item Company", {"parent": item.name}) + 1,
        }).db_insert()
