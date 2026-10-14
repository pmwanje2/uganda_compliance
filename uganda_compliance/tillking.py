"""EFRIS invoice-number restore guard, ported from ERP Champions (930afc2)."""

import re

import frappe
from frappe import _
from frappe.model.naming import NamingSeries
from frappe.utils import add_days, cint, getdate

# ---------------------------------------------------------------------------
# After a restore from backup
# ---------------------------------------------------------------------------

INVOICE_DOCTYPES = ("Sales Invoice", "POS Invoice")
RESTORE_LOOKUP_DAYS = 60
T106_PAGE_SIZE = 99
T106_MAX_PAGES = 50


def advance_series_after_restore(company, days=RESTORE_LOOKUP_DAYS, dry_run=True):
	"""Move the Sales/POS Invoice naming series past every invoice URA fiscalised for the
	company's device in the last ``days`` days.

	A site restored from a backup forgets the invoices fiscalised after the backup, and
	its series counters go back with it: new sales would reuse those invoice names (and
	Seller Reference Nos.), which URA rejects. Run this before reopening the tills.
	It reads URA (T106) only; ``dry_run`` (the default) changes nothing. Each counter
	only ever moves forward, and every change is recorded as a Version of the Series.

	Returns ``{series prefix: {"current", "last_fiscalised", "new"}}``.
	"""
	from uganda_compliance.efris.efris_queue import get_efris_settings

	settings = get_efris_settings(company)
	if not settings:
		frappe.throw(_("EFRIS is not enabled for {0}").format(company))

	own_prefix = f"{settings.seller_reference_prefix}-" if settings.seller_reference_prefix else ""
	series_prefixes = _invoice_series_prefixes()
	last = {}
	for reference in _fiscalised_references(company, settings, cint(days)):
		if not reference.startswith(own_prefix):
			continue  # another system on the same TIN
		name = re.sub(r"-R\d+$", "", reference[len(own_prefix) :])
		prefix = max((p for p in series_prefixes if name.startswith(p)), key=len, default=None)
		number = prefix and re.match(r"\d+", name[len(prefix) :])
		if number:
			last[prefix] = max(last.get(prefix, 0), int(number.group()))

	result = {}
	for prefix, number in sorted(last.items()):
		current = cint(frappe.db.get_value("Series", prefix, "current", order_by="name", for_update=True))
		result[prefix] = {"current": current, "last_fiscalised": number, "new": max(current, number)}
		if number > current and not cint(dry_run):
			_set_series_counter(prefix, current, number)

	frappe.logger("uganda_compliance").info(
		f"advance_series_after_restore {company} dry_run={cint(dry_run)}: {result}"
	)
	return result


def _invoice_series_prefixes():
	"""Counter keys (tabSeries names): this year's Sales/POS Invoice series, and every
	existing counter (earlier years). Only this site's invoice names are matched."""
	series = frappe.qb.DocType("Series")  # a table, not a DocType
	prefixes = set(frappe.qb.from_(series).select(series.name).run(pluck=True))
	for doctype in INVOICE_DOCTYPES:
		options = frappe.get_meta(doctype).get_field("naming_series").options or ""
		prefixes.update(NamingSeries(o.strip()).get_prefix() for o in options.split("\n") if o.strip())
	return prefixes


def _fiscalised_references(company, settings, days):
	"""Seller Reference Nos. of the invoices URA issued on the company's device (T106, paged)."""
	from uganda_compliance.efris.api_classes import e_invoice as e_invoice_api

	references = []
	for page in range(1, T106_MAX_PAGES + 1):
		ok, response = e_invoice_api.make_post(
			interfaceCode="T106",
			content={
				"deviceNo": settings.device_no or "",
				"invoiceType": "1",
				"startDate": str(add_days(getdate(), -days)),
				"endDate": str(add_days(getdate(), 1)),
				"pageNo": str(page),
				"pageSize": str(T106_PAGE_SIZE),
			},
			company_name=company,
			reference_doc_type="E Invoicing Settings",
			reference_document=settings.name,
		)
		if not ok or not isinstance(response, dict):
			# A partial list could leave a counter too low: fail rather than advance.
			frappe.throw(_("EFRIS invoice lookup (T106) failed: {0}").format(response))
		references += [r["referenceNo"] for r in response.get("records") or [] if r.get("referenceNo")]
		if page >= cint((response.get("page") or {}).get("pageCount")):
			return references
	frappe.throw(_("More than {0} pages of invoices at URA; use fewer days").format(T106_MAX_PAGES))


def _set_series_counter(prefix, old, new):
	series = frappe.qb.DocType("Series")
	if frappe.db.get_value("Series", prefix, "name", order_by="name") is not None:
		frappe.qb.update(series).set(series.current, new).where(series.name == prefix).run()
	else:
		frappe.qb.into(series).insert(prefix, new).columns("name", "current").run()
	version = frappe.new_doc("Version")
	version.ref_doctype = "Series"
	version.docname = prefix
	version.data = frappe.as_json({"changed": [["current", old, new]]})
	version.flags.ignore_links = True  # Series is not a real doctype
	version.insert(ignore_permissions=True)
