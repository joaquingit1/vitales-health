"""Create runtime credentials privately from their first filesystem appearance.

Windows uses a protected DACL for the object's owner and LocalSystem at CreateFileW.
POSIX uses exclusive 0600 creation. This is not a private-record key vault.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from crew_health.model.domain.release import ModelRefused


class SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("descriptor", ctypes.c_void_p),
        ("inherit", ctypes.c_int32),
    ]


def _windows_libraries() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    if sys.platform != "win32":
        raise ModelRefused("runtime_key_permissions")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer, dword = ctypes.c_void_p, ctypes.c_uint32
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        dword,
        ctypes.POINTER(pointer),
        ctypes.POINTER(dword),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = ctypes.c_int32
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        dword,
        dword,
        ctypes.POINTER(SecurityAttributes),
        dword,
        dword,
        pointer,
    ]
    kernel.CreateFileW.restype = pointer
    kernel.CloseHandle.argtypes = [pointer]
    kernel.CloseHandle.restype = ctypes.c_int32
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    return advapi, kernel


def create_runtime_key_file(path: Path) -> int:
    if sys.platform != "win32":
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    import msvcrt

    advapi, kernel = _windows_libraries()
    descriptor = ctypes.c_void_p()
    # Protected DACL: only Owner Rights and LocalSystem receive full access.
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        "D:P(A;;FA;;;OW)(A;;FA;;;SY)", 1, ctypes.byref(descriptor), None
    ):
        raise ModelRefused("runtime_key_permissions")
    handle = None
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, 0)
        # GENERIC_WRITE, FILE_SHARE_READ, CREATE_NEW, FILE_ATTRIBUTE_NORMAL. Never inherit the handle.
        handle = kernel.CreateFileW(str(path), 0x40000000, 1, ctypes.byref(attributes), 1, 0x80, None)
    finally:
        if kernel.LocalFree(descriptor):
            if handle not in (None, ctypes.c_void_p(-1).value):
                kernel.CloseHandle(handle)
            raise ModelRefused("runtime_key_permissions")
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ModelRefused("runtime_key_permissions")
    try:
        # On success, ownership transfers to the descriptor and os.fdopen closes it.
        return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except OSError:
        if not kernel.CloseHandle(handle):
            raise ModelRefused("runtime_key_permissions") from None
        raise ModelRefused("runtime_key_permissions") from None
