#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
磁盘优先级控制

对齐 System Informer 的磁盘优先级：对卷设备句柄调用
NtSetInformationFile(FileIoPriorityHintInformation)，把该盘上的 I/O
降到 Very Low（最低）。设置是易失的，重启后失效，因此由服务周期性重申。
"""

import ctypes
import string
import threading
import time
from ctypes import wintypes
from typing import Dict, List, Optional

from utils.logger import logger

# FileInformationClass
FileIoPriorityHintInformation = 43

# FILE_IO_PRIORITY_HINT，与 System Informer / ntddk 一致
IO_PRIORITY_VERY_LOW = 0
IO_PRIORITY_LOW = 1
IO_PRIORITY_NORMAL = 2
IO_PRIORITY_HIGH = 3

PRIORITY_NAMES = {
    IO_PRIORITY_VERY_LOW: "最低 (Very Low)",
    IO_PRIORITY_LOW: "低 (Low)",
    IO_PRIORITY_NORMAL: "正常 (Normal)",
    IO_PRIORITY_HIGH: "高 (High)",
}

# 卷句柄只需要读属性。GENERIC_READ 在非提权进程上会返回 ERROR_ACCESS_DENIED。
FILE_READ_ATTRIBUTES = 0x0080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

DRIVE_TYPE_NAMES = {
    DRIVE_UNKNOWN: "未知",
    DRIVE_NO_ROOT_DIR: "无根目录",
    DRIVE_REMOVABLE: "可移动磁盘",
    DRIVE_FIXED: "本地磁盘",
    DRIVE_REMOTE: "网络驱动器",
    DRIVE_CDROM: "光驱",
    DRIVE_RAMDISK: "内存盘",
}


class FILE_IO_PRIORITY_HINT_INFORMATION(ctypes.Structure):
    """NtSetInformationFile 的 FileIoPriorityHintInformation 缓冲区"""

    _fields_ = [("PriorityHint", ctypes.c_ulong)]


class DiskPriorityManager:
    """把指定盘符的卷 I/O 优先级降到最低"""

    def __init__(self):
        self.ntdll = ctypes.WinDLL("ntdll.dll", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        self._init_api()

    def _init_api(self):
        self.NtSetInformationFile = self.ntdll.NtSetInformationFile
        self.NtSetInformationFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,  # PIO_STATUS_BLOCK
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.c_int,
        ]
        self.NtSetInformationFile.restype = ctypes.c_ulong

        self.NtQueryInformationFile = self.ntdll.NtQueryInformationFile
        self.NtQueryInformationFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.c_int,
        ]
        self.NtQueryInformationFile.restype = ctypes.c_ulong

        self.CreateFileW = self.kernel32.CreateFileW
        self.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self.CreateFileW.restype = ctypes.c_void_p

        self.GetDriveTypeW = self.kernel32.GetDriveTypeW
        self.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        self.GetDriveTypeW.restype = wintypes.UINT

        self.GetVolumeInformationW = self.kernel32.GetVolumeInformationW
        self.GetVolumeInformationW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        self.GetVolumeInformationW.restype = wintypes.BOOL

        self.CloseHandle = self.kernel32.CloseHandle
        self.CloseHandle.argtypes = [ctypes.c_void_p]
        self.CloseHandle.restype = wintypes.BOOL

    def list_disks(self) -> List[Dict]:
        """列出当前存在的盘符及其类型、卷标"""
        disks = []
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            drive_type = self.GetDriveTypeW(root)
            if drive_type in (DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR):
                continue

            label_buf = ctypes.create_unicode_buffer(256)
            fs_buf = ctypes.create_unicode_buffer(256)
            serial = wintypes.DWORD()
            max_comp = wintypes.DWORD()
            flags = wintypes.DWORD()
            has_info = bool(
                self.GetVolumeInformationW(
                    root,
                    label_buf,
                    256,
                    ctypes.byref(serial),
                    ctypes.byref(max_comp),
                    ctypes.byref(flags),
                    fs_buf,
                    256,
                )
            )

            disks.append(
                {
                    "letter": letter,
                    "root": root,
                    "device": f"\\\\.\\{letter}:",
                    "type": drive_type,
                    "type_name": DRIVE_TYPE_NAMES.get(drive_type, "未知"),
                    "label": label_buf.value if has_info else "",
                    "filesystem": fs_buf.value if has_info else "",
                }
            )
        return disks

    def set_disk_priority(self, letter: str, priority: int = IO_PRIORITY_VERY_LOW) -> bool:
        """
        将指定盘符的磁盘 I/O 优先级设为给定等级。

        Args:
            letter: 盘符，如 "D" 或 "D:"
            priority: FILE_IO_PRIORITY_HINT，默认最低
        """
        letter = (letter or "").strip().rstrip(":\\").upper()
        if len(letter) != 1 or letter not in string.ascii_uppercase:
            logger.error(f"无效的盘符: {letter!r}")
            return False
        if priority not in PRIORITY_NAMES:
            logger.error(f"无效的磁盘优先级: {priority}")
            return False

        device = f"\\\\.\\{letter}:"
        handle = self.CreateFileW(
            device,
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if not handle or handle == INVALID_HANDLE_VALUE:
            error_code = ctypes.get_last_error()
            logger.error(f"无法打开磁盘 {device}，错误码: {error_code}")
            return False

        io_status = (ctypes.c_ulonglong * 2)()
        info = FILE_IO_PRIORITY_HINT_INFORMATION(priority)
        try:
            status = self.NtSetInformationFile(
                handle,
                ctypes.byref(io_status),
                ctypes.byref(info),
                ctypes.sizeof(info),
                FileIoPriorityHintInformation,
            )
            if status != 0:
                logger.error(
                    f"设置磁盘 {letter}: 优先级失败，NTSTATUS=0x{status & 0xFFFFFFFF:08X}"
                )
                return False

            logger.debug(f"已将磁盘 {letter}: 的 I/O 优先级设为 {PRIORITY_NAMES[priority]}")
            return True
        except Exception as exc:
            logger.error(f"设置磁盘 {letter}: 优先级时发生错误: {exc}")
            return False
        finally:
            self.CloseHandle(handle)

    def set_disks_to_lowest(self, letters: List[str]) -> Dict[str, bool]:
        """把一组盘符全部降到最低优先级"""
        results = {}
        for letter in letters:
            normalized = (letter or "").strip().rstrip(":\\").upper()
            if not normalized:
                continue
            results[normalized] = self.set_disk_priority(normalized, IO_PRIORITY_VERY_LOW)
        return results

    def query_disk_priority(self, letter: str) -> Optional[int]:
        """读取卷当前的 I/O 优先级提示，失败时返回 None"""
        letter = (letter or "").strip().rstrip(":\\").upper()
        if len(letter) != 1:
            return None

        device = f"\\\\.\\{letter}:"
        handle = self.CreateFileW(
            device,
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if not handle or handle == INVALID_HANDLE_VALUE:
            return None

        io_status = (ctypes.c_ulonglong * 2)()
        info = FILE_IO_PRIORITY_HINT_INFORMATION()
        try:
            status = self.NtQueryInformationFile(
                handle,
                ctypes.byref(io_status),
                ctypes.byref(info),
                ctypes.sizeof(info),
                FileIoPriorityHintInformation,
            )
            if status != 0:
                return None
            return int(info.PriorityHint)
        except Exception:
            return None
        finally:
            self.CloseHandle(handle)


class DiskPriorityService:
    """周期性把配置中的目标磁盘重新降到最低优先级"""

    def __init__(self, config_manager, check_interval: int = 30):
        self.config_manager = config_manager
        self.manager = get_disk_priority_manager()
        self.check_interval = check_interval
        self.running = False
        self.thread = None

    def start_service(self) -> bool:
        if self.running:
            return False
        self.running = True
        self.thread = threading.Thread(target=self._service_loop, daemon=True)
        self.thread.start()
        return True

    def stop_service(self) -> bool:
        if not self.running:
            return False
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(1.0)
        return True

    def _service_loop(self):
        while self.running:
            try:
                self.apply_configured_disks()
            except Exception as exc:
                logger.error(f"磁盘优先级服务出错: {exc}")

            for _ in range(self.check_interval):
                if not self.running:
                    break
                time.sleep(1)

    def apply_configured_disks(self) -> Dict[str, bool]:
        letters = getattr(self.config_manager, "lowest_priority_disks", None) or []
        if not letters:
            return {}
        results = self.manager.set_disks_to_lowest(letters)
        ok = sum(1 for success in results.values() if success)
        if results:
            logger.debug(f"磁盘最低优先级已重申: {ok}/{len(results)}")
        return results


_disk_priority_manager = None
_disk_priority_service = None


def get_disk_priority_manager() -> DiskPriorityManager:
    global _disk_priority_manager
    if _disk_priority_manager is None:
        _disk_priority_manager = DiskPriorityManager()
    return _disk_priority_manager


def get_disk_priority_service(config_manager=None) -> Optional[DiskPriorityService]:
    global _disk_priority_service
    if _disk_priority_service is None and config_manager is not None:
        _disk_priority_service = DiskPriorityService(config_manager)
    return _disk_priority_service
