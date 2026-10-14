import unittest

from uganda_compliance import hooks


class TestQueueHooks(unittest.TestCase):
	def test_paid_pos_invoice_is_queued_on_submit(self):
		self.assertEqual(
			hooks.doc_events.get("POS Invoice", {}).get("on_submit"),
			"uganda_compliance.efris.efris_queue.on_submit_invoice",
		)
