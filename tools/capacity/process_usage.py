"""Process resource measurements; never label application CPU as database CPU."""

import os


def process_usage():
    times = os.times()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Memory(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "peak_working_set",
                    "working_set",
                    "peak_paged",
                    "paged",
                    "peak_nonpaged",
                    "nonpaged",
                    "pagefile",
                    "peak_pagefile",
                )
            ]

        handle = ctypes.windll.kernel32.GetCurrentProcess
        handle.restype = wintypes.HANDLE
        read = ctypes.windll.psapi.GetProcessMemoryInfo
        read.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
        data = Memory()
        data.cb = ctypes.sizeof(data)
        if not read(handle(), ctypes.byref(data), data.cb):
            raise OSError("Cannot measure owned process memory")
        peak = data.peak_working_set
    else:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    return {
        "scope": "current_process",
        "cpu_seconds": round(times.user + times.system, 3),
        "peak_rss_bytes": peak,
    }
