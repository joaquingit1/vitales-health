"""Atomic activation of fully validated immutable profiles."""

from collections.abc import Callable
from threading import Lock

from jsonschema.exceptions import ValidationError

from crew_health_device.domain.errors import ProfileError
from crew_health_device.domain.profile import DeviceProfile


class ProfileStore:
    def __init__(self, loader: Callable[[], DeviceProfile]) -> None:
        self._loader = loader
        self._profile = loader()
        self._lock = Lock()

    def current(self) -> DeviceProfile:
        with self._lock:
            return self._profile

    def reload(self) -> bool:
        try:
            candidate = self._loader()
        except ProfileError, ValidationError:
            return False
        with self._lock:
            self._profile = candidate
        return True
