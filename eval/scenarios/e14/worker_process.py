"""Start a worker and stop it with every process it started, within a fixed bound on each OS.

On Windows the virtual environment's python.exe is CPython's venv launcher (PC/venvlauncher.c, copied by uv): it
starts the base interpreter as a child, places that child in a kill-on-close job of its own and waits for it.
Terminating the launcher only closes that job handle, and the kernel then terminates the interpreter
asynchronously, so the launcher can be reaped while the interpreter still listens on the worker port. Here the
launcher starts suspended inside a job object this harness owns, before it can start anything. Stopping
terminates that job and waits for every member to exit. On POSIX the venv interpreter is the process itself.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import IO

STOP_TIMEOUT_SECONDS = 5.0
# The launcher and its interpreter; the bound fails loudly if a worker ever starts more.
MAX_TREE_PROCESSES = 16
POLL_SECONDS = 0.02


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    CREATE_SUSPENDED = 0x00000004
    PROCESS_TERMINATE = 0x0001
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_SUSPEND_RESUME = 0x0800
    SYNCHRONIZE = 0x00100000
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
    JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    WAIT_OBJECT_0 = 0

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class Accounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class ProcessIdList(ctypes.Structure):
        _fields_ = [
            ("NumberOfAssignedProcesses", wintypes.DWORD),
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_size_t * MAX_TREE_PROCESSES),
        ]

    def kernel32() -> ctypes.WinDLL:
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        library.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        library.CreateJobObjectW.restype = wintypes.HANDLE
        library.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        library.SetInformationJobObject.restype = wintypes.BOOL
        library.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPDWORD,
        )
        library.QueryInformationJobObject.restype = wintypes.BOOL
        library.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        library.AssignProcessToJobObject.restype = wintypes.BOOL
        library.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        library.TerminateJobObject.restype = wintypes.BOOL
        library.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        library.OpenProcess.restype = wintypes.HANDLE
        library.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        library.WaitForSingleObject.restype = wintypes.DWORD
        library.CloseHandle.argtypes = (wintypes.HANDLE,)
        library.CloseHandle.restype = wintypes.BOOL
        return library

    def win_error(action: str) -> RuntimeError:
        return RuntimeError(f"{action} failed: Windows error {ctypes.get_last_error()}")

    def resume(process: int) -> None:
        # subprocess closes the primary thread handle, so the whole process is resumed (as psutil does).
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        ntdll.NtResumeProcess.restype = ctypes.c_long
        status = int(ntdll.NtResumeProcess(process))
        if status != 0:
            raise RuntimeError(f"worker launcher could not be resumed: NTSTATUS {status & 0xFFFFFFFF:#010x}")


class WorkerProcess:
    """A started worker. `stop` returns only when no process of its tree remains."""

    def __init__(self, command: list[str], *, cwd: Path, env: dict[str, str], output: IO[bytes]) -> None:
        self.job: int | None = None
        if sys.platform == "win32":
            self.process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_SUSPENDED,
            )
            try:
                self.job = self.contain()
            except BaseException:
                self.process.kill()
                self.process.wait(timeout=STOP_TIMEOUT_SECONDS)
                raise
        else:
            self.process = subprocess.Popen(
                command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT
            )

    def poll(self) -> int | None:
        return self.process.poll()

    def stop(self) -> None:
        if sys.platform == "win32":
            self.stop_tree()
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=STOP_TIMEOUT_SECONDS)

    if sys.platform == "win32":

        def contain(self) -> int:
            api = kernel32()
            job = api.CreateJobObjectW(None, None)
            if not job:
                raise win_error("CreateJobObjectW")
            try:
                limits = ExtendedLimits()
                limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if not api.SetInformationJobObject(
                    job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
                ):
                    raise win_error("SetInformationJobObject")
                rights = PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME
                handle = api.OpenProcess(rights, False, self.process.pid)
                if not handle:
                    raise win_error("OpenProcess")
                try:
                    if not api.AssignProcessToJobObject(job, handle):
                        raise win_error("AssignProcessToJobObject")
                    resume(handle)
                finally:
                    api.CloseHandle(handle)
            except BaseException:
                api.CloseHandle(job)
                raise
            return int(job)

        def members(self, api: ctypes.WinDLL, job: int) -> list[int]:
            listed = ProcessIdList()
            if not api.QueryInformationJobObject(
                job, JOB_OBJECT_BASIC_PROCESS_ID_LIST, ctypes.byref(listed), ctypes.sizeof(listed), None
            ):
                raise win_error("QueryInformationJobObject(process list)")
            return [int(listed.ProcessIdList[index]) for index in range(listed.NumberOfProcessIdsInList)]

        def active(self, api: ctypes.WinDLL, job: int) -> int:
            accounting = Accounting()
            if not api.QueryInformationJobObject(
                job,
                JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(accounting),
                ctypes.sizeof(accounting),
                None,
            ):
                raise win_error("QueryInformationJobObject(accounting)")
            return int(accounting.ActiveProcesses)

        def stop_tree(self) -> None:
            job = self.job
            if job is None:
                return
            api = kernel32()
            deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
            waits = [api.OpenProcess(SYNCHRONIZE, False, pid) for pid in self.members(api, job)]
            try:
                if not api.TerminateJobObject(job, 1):
                    raise win_error("TerminateJobObject")
                for handle in waits:
                    if not handle:
                        continue  # already exited before it could be opened
                    remaining = max(0, int((deadline - time.monotonic()) * 1000))
                    if api.WaitForSingleObject(handle, remaining) != WAIT_OBJECT_0:
                        raise RuntimeError("worker process tree did not exit within the bounded stop wait")
                while self.active(api, job) > 0:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "worker job still has active processes after the bounded stop wait"
                        )
                    time.sleep(POLL_SECONDS)
            finally:
                for handle in waits:
                    if handle:
                        api.CloseHandle(handle)
            self.process.wait(timeout=max(0.0, deadline - time.monotonic()))
            api.CloseHandle(job)
            self.job = None
