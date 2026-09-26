#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
进程磁盘读写优先级

对齐 System Informer 的磁盘优先级：对进程调用
NtSetInformationProcess(ProcessIoPriority = Very Low)。
只影响目标进程自己的磁盘读写排队，不改变磁盘或系统的优先级。
"""

import ctypes
from ctypes import wintypes

import psutil

from utils.logger import logger

PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_INFORMATION = 0x0400

# PROCESSINFOCLASS
ProcessIoPriority = 33

# IO_PRIORITY_HINT
IO_PRIORITY_VERY_LOW = 0

SGUARD_PROCESS_NAME = "sguard64.exe"


class ProcessDiskPriority:
    """设置指定进程的磁盘读写优先级"""

    def __init__(self):
        self.ntdll = ctypes.WinDLL("ntdll.dll", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

        self.NtSetInformationProcess = self.ntdll.NtSetInformationProcess
        self.NtSetInformationProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        self.NtSetInformationProcess.restype = ctypes.c_ulong

        self.OpenProcess = self.kernel32.OpenProcess
        self.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.OpenProcess.restype = ctypes.c_void_p

        self.CloseHandle = self.kernel32.CloseHandle
        self.CloseHandle.argtypes = [ctypes.c_void_p]
        self.CloseHandle.restype = wintypes.BOOL

    def set_process_disk_priority(self, process_id: int, priority: int = IO_PRIORITY_VERY_LOW) -> bool:
        """把一个进程的磁盘读写优先级降到最低"""
        handle = self.OpenProcess(
            PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION,
            False,
            process_id,
        )
        if not handle:
            logger.error(
                f"无法打开进程(PID={process_id})以设置磁盘优先级，错误码: {ctypes.get_last_error()}"
            )
            return False

        priority_value = ctypes.c_int(priority)
        try:
            status = self.NtSetInformationProcess(
                handle,
                ProcessIoPriority,
                ctypes.byref(priority_value),
                ctypes.sizeof(priority_value),
            )
            if status != 0:
                logger.error(
                    f"设置进程(PID={process_id})磁盘优先级失败，NTSTATUS=0x{status & 0xFFFFFFFF:08X}"
                )
                return False

            logger.debug(f"已将进程(PID={process_id})的磁盘读写优先级设为最低")
            return True
        except Exception as exc:
            logger.error(f"设置进程(PID={process_id})磁盘优先级时发生错误: {exc}")
            return False
        finally:
            self.CloseHandle(handle)

    def set_process_disk_priority_by_name(self, process_name: str) -> int:
        """按进程名把匹配进程的磁盘读写优先级降到最低，返回成功数量"""
        success_count = 0
        target = (process_name or "").lower()
        if target != SGUARD_PROCESS_NAME:
            logger.debug(f"跳过非扫盘进程的磁盘优先级设置: {process_name}")
            return 0

        try:
            for proc in psutil.process_iter(["pid", "name"]):
                name = (proc.info.get("name") or "").lower()
                if name != target:
                    continue
                if self.set_process_disk_priority(proc.info["pid"]):
                    success_count += 1
        except Exception as exc:
            logger.error(f"按名称设置磁盘优先级时发生错误: {exc}")
        return success_count


_process_disk_priority = None


def get_process_disk_priority() -> ProcessDiskPriority:
    global _process_disk_priority
    if _process_disk_priority is None:
        _process_disk_priority = ProcessDiskPriority()
    return _process_disk_priority
