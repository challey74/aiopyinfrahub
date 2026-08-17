from aiopyinfrahub.models import KIND_MODELS, register_model
from aiopyinfrahub.response import Record


class Device(Record):
    """A downstream Record subclass for the fake's device kind."""

    def __str__(self) -> str:
        return f"device {super().__str__()}"


def test_unregistered_kind_falls_back_to_record(ih):
    assert ih.TestingDevice.record_class is Record


async def test_register_model_is_used_by_the_endpoint(ih):
    register_model("TestingDevice", Device)
    try:
        assert ih.TestingDevice.record_class is Device
        device = await ih.TestingDevice.get("sw-1")
        assert isinstance(device, Device)
        assert str(device) == "device sw-1"
    finally:
        del KIND_MODELS["TestingDevice"]


async def test_registration_is_read_per_attribute_access(ih):
    """Endpoints are built fresh, so registering later still takes effect."""
    endpoint = ih.TestingDevice
    register_model("TestingDevice", Device)
    try:
        assert endpoint.record_class is Record
        assert ih.TestingDevice.record_class is Device
    finally:
        del KIND_MODELS["TestingDevice"]


async def test_nested_peers_stay_base_records(ih):
    """A nested peer has no endpoint context to resolve a subclass from."""
    register_model("TestingDevice", Device)
    try:
        device = await ih.TestingDevice.get("sw-1")
        assert device is not None
        assert type(device.site) is Record
    finally:
        del KIND_MODELS["TestingDevice"]
