from __future__ import annotations

import asyncio
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.event.filter import PermissionType, command, permission_type
from astrbot.api.star import Context, Star

DATA_DIR = Path(__file__).resolve().parents[2]
BACKUP_DIR = DATA_DIR / "backups" / "config"
DB_PATH = DATA_DIR / "data_v4.db"
TAG = "[config_backup]"


class ConfigBackupPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = config
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        try:
            self._task = asyncio.create_task(self._loop())
        except RuntimeError:
            logger.warning(TAG + " 无运行中的事件循环，定时任务未启动")

    # ---------- 定时任务 ----------

    async def _loop(self):
        if bool(self.cfg.get("backup_on_start", True)):
            await asyncio.sleep(30)
            await self._safe_backup("启动时")
        interval = max(float(self.cfg.get("interval_hours", 6)), 0.05) * 3600
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
            await self._safe_backup("定时")

    async def _safe_backup(self, reason: str):
        try:
            name, size, count = await asyncio.to_thread(self._do_backup)
            logger.info("%s %s备份完成: %s (%d 个文件, %.1f KB)",
                        TAG, reason, name, count, size / 1024)
        except Exception as e:
            logger.error("%s %s备份失败: %s: %s", TAG, reason, type(e).__name__, e)

    # ---------- 备份实现 ----------

    def _collect(self) -> list[Path]:
        items: list[Path] = []
        for pattern in self.cfg.get("include", ["cmd_config.json", "config/*.json"]):
            for p in DATA_DIR.glob(str(pattern)):
                if p.is_file() and p.stat().st_size > 0:
                    items.append(p)
        uniq = {p.resolve(): p for p in items}
        return sorted(uniq.values())

    def _do_backup(self) -> tuple[str, int, int]:
        files = self._collect()
        with_db = bool(self.cfg.get("include_db", False))
        if not files and not with_db:
            raise RuntimeError("没有找到可备份的配置文件")

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final = BACKUP_DIR / ("config-%s.zip" % stamp)
        tmp = BACKUP_DIR / (".config-%s.zip.tmp" % stamp)

        count = 0
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in files:
                    zf.write(p, str(p.relative_to(DATA_DIR)))
                    count += 1
                if with_db and DB_PATH.exists():
                    self._write_db(zf)
                    count += 1
            shutil.move(str(tmp), str(final))
        finally:
            if tmp.exists():
                tmp.unlink()

        self._prune()
        return final.name, final.stat().st_size, count

    def _write_db(self, zf: zipfile.ZipFile):
        """用 SQLite 在线备份接口导出，避免直接拷贝正在写入的库文件。"""
        db_tmp = BACKUP_DIR / (".data_v4.db.tmp")
        if db_tmp.exists():
            db_tmp.unlink()
        src = sqlite3.connect("file:%s?mode=ro" % str(DB_PATH).replace("\\", "/"), uri=True)
        dst = sqlite3.connect(str(db_tmp))
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
            src.close()
        try:
            zf.write(db_tmp, "data_v4.db")
        finally:
            db_tmp.unlink()

    def _prune(self):
        keep = max(int(self.cfg.get("keep_count", 30)), 1)
        backups = sorted(BACKUP_DIR.glob("config-*.zip"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[keep:]:
            try:
                old.unlink()
                logger.info("%s 清理旧备份: %s", TAG, old.name)
            except OSError as e:
                logger.warning("%s 清理失败 %s: %s", TAG, old.name, e)

    # ---------- 指令 ----------

    @command("备份配置")
    @permission_type(PermissionType.ADMIN)
    async def backup_now(self, event: AstrMessageEvent):
        """立即执行一次配置备份"""
        try:
            name, size, count = await asyncio.to_thread(self._do_backup)
            yield event.plain_result(
                "配置备份完成\n文件: %s\n内容: %d 个文件\n大小: %.1f KB\n目录: %s"
                % (name, count, size / 1024, BACKUP_DIR)
            )
        except Exception as e:
            yield event.plain_result("配置备份失败: %s: %s" % (type(e).__name__, e))

    @command("备份列表")
    @permission_type(PermissionType.ADMIN)
    async def backup_list(self, event: AstrMessageEvent):
        """查看最近的配置备份"""
        backups = sorted(BACKUP_DIR.glob("config-*.zip"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        if not backups:
            yield event.plain_result("还没有任何备份")
            return
        keep = max(int(self.cfg.get("keep_count", 30)), 1)
        lines = ["最近 %d 份配置备份（最多保留 %d 份）:" % (len(backups[:10]), keep)]
        for p in backups[:10]:
            lines.append("%s  %.1f KB  %s"
                         % (datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M"),
                            p.stat().st_size / 1024, p.name))
        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info(TAG + " 已停止")
