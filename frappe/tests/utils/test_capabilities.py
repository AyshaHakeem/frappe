import os
import unittest
from enum import StrEnum


class TestService(StrEnum):
	"""Services that may be deliberately absent from a test environment."""

	WEB_SERVER = "web server"
	BACKGROUND_WORKER = "background worker"


_SERVICE_ENVIRONMENT_VARIABLE = {
	TestService.WEB_SERVER: "FRAPPE_TEST_WEB_SERVER",
	TestService.BACKGROUND_WORKER: "FRAPPE_TEST_BACKGROUND_WORKER",
}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def is_test_service_available(service: TestService) -> bool:
	"""Return whether ``service`` is available to this test process.
	Services are available by default so existing local and MariaDB test runs
	keep their current behaviour.  CI can opt out with the corresponding
	``FRAPPE_TEST_*`` environment variable.
	"""
	try:
		environment_variable = _SERVICE_ENVIRONMENT_VARIABLE[service]
	except KeyError as error:
		raise ValueError(f"Unknown test service: {service!r}") from error

	value = os.environ.get(environment_variable)
	if value is None:
		return True

	normalized_value = value.strip().lower()
	if normalized_value in _TRUE_VALUES:
		return True
	if normalized_value in _FALSE_VALUES:
		return False

	valid_values = ", ".join(sorted(_TRUE_VALUES | _FALSE_VALUES))
	raise ValueError(
		f"{environment_variable} must be one of {valid_values}; received {value!r}."
	)


def requires_test_service(service: TestService):
	"""Skip a test only when its required external service is unavailable."""
	return unittest.skipUnless(
		is_test_service_available(service), f"Requires a running Frappe test {service.value}."
	)
