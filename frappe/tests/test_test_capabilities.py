import os
import unittest
from unittest.mock import patch

from frappe.tests.utils.test_capabilities import (
	TestService,
	is_test_service_available,
	requires_test_service,
)


class TestTestCapabilities(unittest.TestCase):
	def test_services_are_available_by_default(self):
		with patch.dict(os.environ, {}, clear=True):
			self.assertTrue(is_test_service_available(TestService.WEB_SERVER))
			self.assertTrue(is_test_service_available(TestService.BACKGROUND_WORKER))

	def test_service_can_be_disabled_with_environment_variable(self):
		with patch.dict(os.environ, {"FRAPPE_TEST_WEB_SERVER": "0"}, clear=True):
			self.assertFalse(is_test_service_available(TestService.WEB_SERVER))

	def test_service_accepts_explicit_true_value(self):
		with patch.dict(os.environ, {"FRAPPE_TEST_BACKGROUND_WORKER": "yes"}, clear=True):
			self.assertTrue(is_test_service_available(TestService.BACKGROUND_WORKER))

	def test_invalid_service_value_fails_loudly(self):
		with patch.dict(os.environ, {"FRAPPE_TEST_WEB_SERVER": "sometimes"}, clear=True):
			with self.assertRaisesRegex(ValueError, "FRAPPE_TEST_WEB_SERVER"):
				is_test_service_available(TestService.WEB_SERVER)

	def test_required_service_marks_test_as_skipped_when_unavailable(self):
		with patch.dict(os.environ, {"FRAPPE_TEST_WEB_SERVER": "off"}, clear=True):
			@requires_test_service(TestService.WEB_SERVER)
			def web_test():
				pass

		self.assertTrue(web_test.__unittest_skip__)
		self.assertEqual(web_test.__unittest_skip_why__, "Requires a running Frappe test web server.")
