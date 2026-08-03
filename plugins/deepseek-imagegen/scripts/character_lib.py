#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""角色卡（v2）：把已核实角色设定存进本机 MySQL，出图时自动注入。

数据只存本机，绝不进入 GitHub 仓库。
默认角色：洛天依 V4 公式服（设定已由 FactGuard 核实）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import image_gen  # noqa: E402


def mysql_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """读取角色卡的 MySQL 连接配置（未配置时复用词库的 MySQL 配置）。"""
    chars = cfg.get("characters") or {}
    if not isinstance(chars, dict):
        chars = {}
    mc = chars.get("mysql") or {}
    if not isinstance(mc, dict) or not mc.get("user"):
        pl = cfg.get("prompt_library") or {}
        mc = (pl.get("mysql") or {}) if isinstance(pl, dict) else {}
    if not isinstance(mc, dict):
        mc = {}
    return {
        "host": str(mc.get("host") or "127.0.0.1"),
        "port": int(mc.get("port") or 3306),
        "user": str(mc.get("user") or ""),
        "password": str(mc.get("password") or ""),
        "db": str(mc.get("db") or "deepseek_imagegen"),
    }


def _connect(cfg: dict[str, Any]):
    try:
        import pymysql  # type: ignore  # noqa: PLC0415
    except ImportError as exc:
        raise image_gen.GenError(
            "角色卡需要 pymysql 库，请先安装：pip install pymysql"
        ) from exc
    mc = mysql_cfg(cfg)
    if not mc["user"]:
        raise image_gen.GenError(
            "未配置 MySQL 账号：请在 ~/.deepseek-imagegen/config.json 的 "
            "characters.mysql 或 prompt_library.mysql 中填写 host/port/user/password/db。"
        )
    try:
        return pymysql.connect(
            host=mc["host"],
            port=mc["port"],
            user=mc["user"],
            password=mc["password"],
            database=mc["db"],
            charset="utf8mb4",
            connect_timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        raise image_gen.GenError(f"连接本机 MySQL 失败：{exc}") from exc


def ensure_table(cfg: dict[str, Any]) -> None:
    """建表（不存在才创建），并写入默认已核实的洛天依 V4 公式服角色卡。"""
    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS characters (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    version VARCHAR(128) NOT NULL DEFAULT '',
                    hair_color VARCHAR(255) NOT NULL DEFAULT '',
                    eye_color VARCHAR(255) NOT NULL DEFAULT '',
                    outfit TEXT,
                    taboos TEXT,
                    verified TINYINT NOT NULL DEFAULT 0,
                    source VARCHAR(128) NOT NULL DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uniq_name_version (name, version)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                "SELECT COUNT(*) FROM characters WHERE name=%s AND version=%s",
                ("洛天依", "V4公式服"),
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    """
                    INSERT INTO characters
                        (name, version, hair_color, eye_color, outfit, taboos, verified, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "洛天依",
                        "V4公式服",
                        "银灰色长发",
                        "绿色瞳孔",
                        "蓝白配色公式服，腰部有中国结装饰，整体为日系少女风",
                        "不得改成Q版/大头比例；不得改变发色瞳色；不得漏掉耳机等标志性元素；不得写错服装配色",
                        1,
                        "factguard",
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def search_character(cfg: dict[str, Any], name: str = "") -> Optional[dict[str, Any]]:
    """按角色名（可选版本）查找角色卡，找不到返回 None。"""
    ensure_table(cfg)
    conn = _connect(cfg)
    try:
        query = (name or "洛天依").strip()
        base_name, version = query, ""
        if "-" in query:
            base_name, _, version = query.partition("-")
            base_name = base_name.strip()
            version = version.strip()
        with conn.cursor() as cur:
            if version:
                cur.execute(
                    """
                    SELECT name, version, hair_color, eye_color, outfit, taboos, verified, source
                    FROM characters
                    WHERE name=%s AND (version=%s OR version=%s)
                    ORDER BY verified DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (base_name, version, ""),
                )
            else:
                cur.execute(
                    """
                    SELECT name, version, hair_color, eye_color, outfit, taboos, verified, source
                    FROM characters
                    WHERE name=%s OR name LIKE %s
                    ORDER BY verified DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (base_name, f"%{base_name}%"),
                )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "name": row[0],
            "version": row[1],
            "hair_color": row[2],
            "eye_color": row[3],
            "outfit": row[4],
            "taboos": row[5],
            "verified": bool(row[6]),
            "source": row[7],
        }
    finally:
        conn.close()


def add_character(
    cfg: dict[str, Any],
    *,
    name: str,
    version: str = "",
    hair_color: str = "",
    eye_color: str = "",
    outfit: str = "",
    taboos: str = "",
    source: str = "factguard",
    verified: bool = True,
) -> dict[str, Any]:
    if not name.strip():
        raise image_gen.GenError("角色名不能为空。")
    ensure_table(cfg)
    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO characters
                    (name, version, hair_color, eye_color, outfit, taboos, verified, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    hair_color=VALUES(hair_color), eye_color=VALUES(eye_color),
                    outfit=VALUES(outfit), taboos=VALUES(taboos),
                    verified=VALUES(verified), source=VALUES(source)
                """,
                (
                    name.strip(),
                    version.strip(),
                    hair_color.strip(),
                    eye_color.strip(),
                    outfit.strip(),
                    taboos.strip(),
                    1 if verified else 0,
                    source.strip() or "factguard",
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "name": name.strip(), "version": version.strip()}


def list_characters(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_table(cfg)
    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT name, version, hair_color, eye_color, verified, source
                FROM characters
                ORDER BY verified DESC, updated_at DESC
                """
            )
            rows = cur.fetchall()
        return [
            {
                "name": r[0],
                "version": r[1],
                "hair_color": r[2],
                "eye_color": r[3],
                "verified": bool(r[4]),
                "source": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()


def cmd_character(args: Any) -> dict[str, Any]:
    cfg = image_gen.load_config()
    action = str(getattr(args, "action", "") or "")
    if action == "init":
        ensure_table(cfg)
        return {
            "ok": True,
            "action": "init",
            "message": "角色表已就绪，并已写入默认角色卡：洛天依 V4公式服（FactGuard 已核实）",
        }
    if action == "add":
        return add_character(
            cfg,
            name=getattr(args, "name", ""),
            version=getattr(args, "version", ""),
            hair_color=getattr(args, "hair_color", ""),
            eye_color=getattr(args, "eye_color", ""),
            outfit=getattr(args, "outfit", ""),
            taboos=getattr(args, "taboos", ""),
            source=getattr(args, "source", "factguard"),
            verified=getattr(args, "verified", False),
        )
    if action == "list":
        return {"ok": True, "action": "list", "characters": list_characters(cfg)}
    if action == "search":
        card = search_character(cfg, getattr(args, "name", ""))
        return {"ok": bool(card), "action": "search", "character": card}
    raise image_gen.GenError(f"未知操作：{action}")


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    image_gen.configure_console_utf8()
    parser = argparse.ArgumentParser(prog="character_lib.py", description="角色卡管理")
    parser.add_argument("action", choices=["init", "add", "list", "search"])
    parser.add_argument("--name", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--hair-color", dest="hair_color", default="")
    parser.add_argument("--eye-color", dest="eye_color", default="")
    parser.add_argument("--outfit", default="")
    parser.add_argument("--taboos", default="")
    parser.add_argument("--source", default="factguard")
    parser.add_argument("--verified", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = cmd_character(args)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result.get("message"):
                print(result["message"])
            for c in result.get("characters") or []:
                flag = "✓" if c.get("verified") else " "
                print(
                    f"[{flag}] {c['name']} {c.get('version') or ''} "
                    f"({c.get('hair_color') or ''} / {c.get('eye_color') or ''})"
                )
            card = result.get("character")
            if card:
                print(json.dumps(card, ensure_ascii=False, indent=2))
        return 0
    except image_gen.GenError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
