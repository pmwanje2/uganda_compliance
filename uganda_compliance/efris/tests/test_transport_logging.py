import unittest
from unittest.mock import Mock, patch

from uganda_compliance.efris.api_classes import request_utils
from uganda_compliance.efris.utils import utils


class TestTransportLogging(unittest.TestCase):
	def test_post_has_bounded_connect_and_read_timeouts(self):
		with patch.object(request_utils.requests, "post", return_value=Mock(text="ok")) as post:
			self.assertEqual(request_utils.post_req("{}", "https://example.invalid"), "ok")
		self.assertEqual(post.call_args.kwargs.get("timeout"), (10, 120))

	def test_info_uses_file_log_not_error_log(self):
		with patch.object(utils.frappe, "log_error") as error_log, patch.object(
			utils.frappe, "logger"
		) as file_log:
			utils.efris_log_info("EFRIS pending")
		error_log.assert_not_called()
		file_log.assert_called_once_with("uganda_compliance", allow_site=True)
		file_log.return_value.info.assert_called_once_with("EFRIS pending")
