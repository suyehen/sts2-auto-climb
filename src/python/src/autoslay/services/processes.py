"""Hold a Windows process handle and verify its executable, script and creation time."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
from ctypes import wintypes


def command_args(command):
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not pointer:
        raise ValueError('PROCESS_IDENTITY_UNVERIFIED')
    try:
        return [pointer[i] for i in range(count.value)]
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel.LocalFree(pointer)


def service_command(args, executable, script):
    """The script must be the Python program, never an argument to -c or -m."""
    index = 3 if args[1:3] == ['-X', 'utf8'] else 1
    expected_tail = ['--no-browser'] if Path(script).name == 'decision_dashboard.py' else []
    return (len(args) > index and Path(args[0]).resolve() == Path(executable).resolve() and
            Path(args[index]).resolve() == Path(script).resolve() and
            args[index+1:] in ([], expected_tail))


class OwnedProcess:
    """No PID-only kill: a reused PID cannot retarget the retained process handle."""
    def __init__(self, pid, executable, script):
        if os.name != 'nt' or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
            raise ValueError('PROCESS_IDENTITY_UNVERIFIED')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        k = self.kernel
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.handle = k.OpenProcess(0x100000 | 0x1000 | 0x0001, False, pid)
        if not self.handle:
            if ctypes.get_last_error() == 87:
                raise ProcessLookupError(pid)
            raise ValueError('PROCESS_IDENTITY_UNVERIFIED')
        try:
            if k.WaitForSingleObject(self.handle, 0) == 0:
                raise ProcessLookupError(pid)
            size = wintypes.DWORD(32768)
            image = ctypes.create_unicode_buffer(size.value)
            created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
            if (not k.QueryFullProcessImageNameW(self.handle, 0, image, ctypes.byref(size)) or
                    not k.GetProcessTimes(self.handle, *[ctypes.byref(t) for t in (created, exited, system, user)])):
                raise ValueError('PROCESS_IDENTITY_UNVERIFIED')
            birth = (created.dwHighDateTime << 32) | created.dwLowDateTime
            # Only an integer PID is interpolated; never execute paths or command-line text.
            query = ("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
                     f"Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}' | "
                     "Select-Object CommandLine,@{n='Created';e={$_.CreationDate.ToUniversalTime().ToFileTimeUtc().ToString()}} | ConvertTo-Json -Compress")
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', query],
                                    capture_output=True, encoding='utf-8', timeout=8,
                                    creationflags=subprocess.CREATE_NO_WINDOW, check=True)
            record = json.loads(result.stdout)
            args = command_args(record['CommandLine'])
            expected_exe, expected_script = Path(executable).resolve(), Path(script).resolve()
            # CIM timestamps have microsecond precision; FILETIME has 100 ns precision.
            if (int(record['Created']) // 10 != birth // 10 or Path(image.value).resolve() != expected_exe or
                    not service_command(args, expected_exe, expected_script)):
                raise ValueError('PROCESS_IDENTITY_UNVERIFIED')
        except BaseException:
            self.close()
            raise

    def terminate(self):
        if self.kernel.WaitForSingleObject(self.handle, 0) == 0:
            return
        if not self.kernel.TerminateProcess(self.handle, 0):
            raise ValueError('SERVICE_STOP_FAILED')
        if self.kernel.WaitForSingleObject(self.handle, 10000) != 0:
            raise ValueError('SERVICE_STOP_TIMEOUT')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
