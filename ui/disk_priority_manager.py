#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
磁盘优先级管理对话框

列出本机磁盘，把选中的磁盘 I/O 优先级降到最低，并写入配置以便持续生效。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.styles import StyleHelper, theme_manager
from utils.disk_priority import (
    IO_PRIORITY_VERY_LOW,
    PRIORITY_NAMES,
    get_disk_priority_manager,
)
from utils.logger import logger


class DiskPriorityDialog(QDialog):
    """选择目标磁盘并降到最低优先级"""

    def __init__(self, parent=None, config_manager=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.manager = get_disk_priority_manager()
        self.disks = []

        theme_manager.theme_changed.connect(self.apply_theme_properties)
        self.setup_ui()
        self.apply_theme_properties()
        self.refresh_disks()

    def setup_ui(self):
        self.setWindowTitle("磁盘优先级")
        self.setMinimumSize(720, 420)
        self.resize(780, 460)

        layout = QVBoxLayout(self)

        self.info_label = QLabel(
            "对齐 System Informer 的磁盘优先级：对选中卷设置 FileIoPriorityHint = Very Low。\n"
            "该设置会降低这张盘上的后台读写抢占，重启后失效，已保存的磁盘会每 30 秒自动重申。\n"
            "系统盘降到最低可能拖慢启动和更新，建议只对扫盘、下载所在的数据盘使用。"
        )
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.disk_table = QTableWidget()
        self.disk_table.setColumnCount(6)
        self.disk_table.setHorizontalHeaderLabels(
            ["盘符", "卷标", "类型", "文件系统", "当前优先级", "操作"]
        )
        self.disk_table.setAlternatingRowColors(True)
        self.disk_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.disk_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.disk_table.setShowGrid(False)
        self.disk_table.setFocusPolicy(Qt.NoFocus)
        self.disk_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.disk_table.verticalHeader().setDefaultSectionSize(46)
        self.disk_table.verticalHeader().setVisible(False)

        header = self.disk_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setMinimumHeight(36)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        header.resizeSection(0, 70)
        header.resizeSection(2, 110)
        header.resizeSection(3, 90)
        header.resizeSection(4, 140)
        header.resizeSection(5, 150)
        layout.addWidget(self.disk_table)

        self.count_label = QLabel("磁盘数量: 0")
        layout.addWidget(self.count_label)

        button_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedSize(90, 34)
        self.refresh_btn.clicked.connect(self.refresh_disks)
        button_layout.addWidget(self.refresh_btn)

        button_layout.addStretch()

        self.close_btn = QPushButton("关闭")
        self.close_btn.setFixedSize(80, 34)
        self.close_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.close_btn)
        layout.addLayout(button_layout)

    def apply_theme_properties(self):
        try:
            StyleHelper.set_button_type(self.refresh_btn, "primary")
            StyleHelper.set_button_type(self.close_btn, "default")
            StyleHelper.set_label_type(self.info_label, "info")
        except Exception as exc:
            logger.error(f"应用磁盘优先级对话框主题失败: {exc}")

    def refresh_disks(self):
        try:
            self.disks = self.manager.list_disks()
        except Exception as exc:
            logger.error(f"枚举磁盘失败: {exc}")
            self.disks = []

        saved = set()
        if self.config_manager:
            saved = {
                str(letter).strip().rstrip(":\\").upper()
                for letter in getattr(self.config_manager, "lowest_priority_disks", [])
            }

        self.disk_table.setRowCount(len(self.disks))
        for row, disk in enumerate(self.disks):
            letter = disk["letter"]
            self._set_item(row, 0, f"{letter}:")
            self._set_item(row, 1, disk.get("label") or "—")
            self._set_item(row, 2, disk.get("type_name") or "未知")
            self._set_item(row, 3, disk.get("filesystem") or "—")

            current = self.manager.query_disk_priority(letter)
            if letter in saved:
                priority_text = "最低 (持续生效)"
            elif current is None:
                priority_text = "默认"
            else:
                priority_text = PRIORITY_NAMES.get(current, "默认")
            self._set_item(row, 4, priority_text)

            action = QWidget()
            action_layout = QHBoxLayout(action)
            action_layout.setContentsMargins(4, 4, 4, 4)

            if letter in saved:
                button = QPushButton("恢复默认")
                StyleHelper.set_button_type(button, "warning")
                button.setToolTip("停止维持最低优先级。当前这次启动内的设置要到重启后才完全恢复。")
                button.clicked.connect(lambda checked=False, drive=letter: self.restore_disk(drive))
            else:
                button = QPushButton("降为最低")
                StyleHelper.set_button_type(button, "danger")
                button.setToolTip("立即把该磁盘 I/O 优先级设为 Very Low，并在之后持续重申")
                button.clicked.connect(lambda checked=False, drive=letter: self.lower_disk(drive))

            button.setFixedHeight(28)
            action_layout.addWidget(button)
            self.disk_table.setCellWidget(row, 5, action)

        self.count_label.setText(f"磁盘数量: {len(self.disks)}    已降为最低: {len(saved)}")

    def _set_item(self, row, column, text):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        self.disk_table.setItem(row, column, item)

    def lower_disk(self, letter: str):
        if not self.config_manager:
            QMessageBox.warning(self, "无法保存", "配置管理器不可用")
            return

        if letter.upper() == "C":
            reply = QMessageBox.question(
                self,
                "确认降低系统盘优先级",
                "C: 通常是系统盘。把它降到最低会拖慢系统自身的磁盘访问。\n确定继续吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        success = self.manager.set_disk_priority(letter, IO_PRIORITY_VERY_LOW)
        if not success:
            QMessageBox.warning(
                self,
                "设置失败",
                f"无法将磁盘 {letter}: 的优先级降为最低。\n请确认程序以管理员身份运行，且该磁盘当前可访问。",
            )
            return

        saved = [
            str(item).strip().rstrip(":\\").upper()
            for item in self.config_manager.lowest_priority_disks
        ]
        if letter.upper() not in saved:
            self.config_manager.lowest_priority_disks.append(letter.upper())

        if self.config_manager.save_config():
            logger.debug(f"磁盘 {letter}: 已加入最低优先级列表")
            QMessageBox.information(
                self,
                "已降为最低",
                f"磁盘 {letter}: 的 I/O 优先级已设为最低 (Very Low)。\n程序运行期间会每 30 秒自动重申。",
            )
        else:
            QMessageBox.warning(self, "保存失败", "优先级已设置，但写入配置失败，重启后不会自动重申。")

        self.refresh_disks()

    def restore_disk(self, letter: str):
        if not self.config_manager:
            return

        normalized = letter.upper()
        self.config_manager.lowest_priority_disks = [
            str(item).strip().rstrip(":\\").upper()
            for item in self.config_manager.lowest_priority_disks
            if str(item).strip().rstrip(":\\").upper() != normalized
        ]

        # 尝试把卷优先级提示恢复为 Normal。部分系统上卷提示是只写的，失败不影响移出列表。
        restored = self.manager.set_disk_priority(normalized, 2)
        if self.config_manager.save_config():
            extra = "" if restored else "\n当前优先级提示未能立即改回，重启后会恢复系统默认。"
            QMessageBox.information(self, "已恢复", f"磁盘 {normalized}: 已移出最低优先级列表。{extra}")
        else:
            QMessageBox.warning(self, "保存失败", "移出列表后保存配置失败")

        self.refresh_disks()

    def closeEvent(self, event):
        try:
            theme_manager.theme_changed.disconnect(self.apply_theme_properties)
        except Exception:
            pass
        event.accept()


def show_disk_priority_manager(parent=None, config_manager=None):
    dialog = DiskPriorityDialog(parent, config_manager)
    return dialog.exec()
