# -*- coding: utf-8 -*-
"""城管台账 — 单账号社区城管巡查台账，Flask 主程序。

移动端优先 · 单账号登录 · 按两个乡镇（饶州街道 / 鄱阳镇）归类 · SQLite 单文件 · 原图保留。
"""
import io
import os
import re
import secrets
import shutil
import sqlite3
import uuid
import zipfile as _zip
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template,
    request, send_file, send_from_directory, session, url_for,
)

import image_utils

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", str(BASE_DIR / "uploads")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# 主账号的 4 位密码：首次初始化写死 0000，改密在「修改密码」页
OWNER_PIN = os.environ.get("OWNER_PIN", "0000").strip() or "0000"

# ---------- 常量配置（增改一处全局生效） ----------
# 组织结构只有两个乡镇：填写、统计、导出都按这两类
TOWNS = ["饶州街道", "鄱阳镇"]

# 老库里 team 存的是中队名，迁移时按前缀归到乡镇
TEAM_TO_TOWN = {"鄱阳镇": "鄱阳镇", "饶州街道": "饶州街道"}

CATEGORIES = [
    "违法搭建", "牛皮癣小广告", "乱堆放杂物", "电动车乱停放",
    "流动摊贩", "出店经营", "毁坏绿化", "占道经营",
    "破坏市政设施", "乱倒垃圾", "噪音扰民", "投诉纠纷", "其他",
]

# 描述模板：按分类给几条常用写法（从真实台账提炼），录入页点一下填入；
# 「其他」是日常事务类。模板里的 X / XX 是占位，填的时候替换成实际情况。
DESCRIPTION_TEMPLATES = {
    "违法搭建": ["X栋X单元顶楼违规搭建阳光房约X平米", "业主侵占公共区域搭建围栏/楼梯"],
    "牛皮癣小广告": ["X栋单元门、楼道口张贴新增小广告X处", "X栋楼道新增牛皮癣，已拍照留存"],
    "乱堆放杂物": ["X栋楼道口堆放旧家具、纸箱等杂物", "装修堆放建筑垃圾未及时清理", "绿化带内堆放杂物"],
    "电动车乱停放": ["X栋门口电动车乱停放，堵塞消防通道"],
    "流动摊贩": ["小区门口流动摊贩占道经营", "流动摊贩占道售卖，已现场劝导"],
    "出店经营": ["XX店出店经营，货物占用人行道"],
    "毁坏绿化": ["业主圈占绿地种菜，毁坏灌木约X平米", "绿化带种菜，异味扰民"],
    "占道经营": ["XX周边摊贩占道经营，早高峰通行受阻"],
    "破坏市政设施": ["X处市政设施损坏（路灯/井盖/健身器材）"],
    "乱倒垃圾": ["绿化带内倾倒建筑垃圾X处", "生活垃圾未入桶，散落在XX处"],
    "噪音扰民": ["XX（广场舞/夜市/装修/水泵）噪音扰民"],
    "投诉纠纷": ["居民反映XX问题（漏水/污水/邻里纠纷），已上门了解", "X栋住户投诉XX，现场调解"],
    "其他": ["查看一户一档资料整改情况", "物业质价评估现场检查", "协同消防检查", "现场办公"],
}

# 整改举措常用短语：销号页点一下填入
RESULT_PHRASES = ["现场清理", "已现场清理", "现场拆除", "现场制止并拆除", "现场调解",
                  "已现场调解", "现场协调", "现场处置", "现场劝导并搬离", "已完成",
                  "已查看", "督促物业整改"]


def town_of(value):
    """把老的中队名归到乡镇；已经是乡镇名或为空则原样返回。"""
    for prefix, town in TEAM_TO_TOWN.items():
        if value.startswith(prefix):
            return town
    return value


def get_setting(key, default=""):
    row = get_db().execute(
        "SELECT value FROM settings WHERE key=?", (key,)
    ).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    get_db().execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    get_db().commit()


def log_action(action, detail):
    """操作日志：记录是谁、做了什么。"""
    u = current_user()
    if not u:
        return
    get_db().execute(
        "INSERT INTO logs(user, action, detail, created_at) VALUES(?,?,?,?)",
        (u["name"], action, detail, now()),
    )
    get_db().commit()


app = Flask(__name__)


@app.context_processor
def inject_globals():
    """所有模板可用：user（当前用户或 None）、towns（两个乡镇）。"""
    return {"user": current_user(), "towns": TOWNS}


def _load_secret_key():
    """会话签名密钥：优先环境变量，没有就生成一个并落盘。

    原来那个出厂默认值等于任何人都能伪造 session cookie，必须去掉。
    落盘是为了重启后不用重新登录；设 SECRET_KEY 环境变量可覆盖。
    """
    env = os.environ.get("SECRET_KEY", "").strip()
    if env:
        return env
    p = DATA_DIR / ".secret_key"
    if p.exists() and p.read_text().strip():
        return p.read_text().strip()
    key = secrets.token_hex(32)
    p.write_text(key)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    print(f"[init] 已生成会话密钥：{p}（设 SECRET_KEY 环境变量可覆盖）")
    return key


app.config["SECRET_KEY"] = _load_secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=180)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 单次上传上限 100MB


# ---------- 数据库 ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATA_DIR / "records.db")
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DATA_DIR / "records.db") as db:
        db.row_factory = sqlite3.Row
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL,          -- 单账号：恒为 super
            title TEXT NOT NULL DEFAULT '',
            pin TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            town TEXT NOT NULL,          -- 饶州街道 / 鄱阳镇
            community TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result TEXT DEFAULT '',
            deadline TEXT NOT NULL DEFAULT '',
            lead_dept TEXT NOT NULL DEFAULT '',
            assist_dept TEXT NOT NULL DEFAULT '',
            reporter TEXT NOT NULL,
            created_at TEXT NOT NULL,
            closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            type TEXT NOT NULL,            -- before / after
            filepath TEXT NOT NULL,        -- 原图相对路径
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS communities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            count INTEGER NOT NULL DEFAULT 1
        );
        -- 案件功能已去掉（改个人台账后不再录入案件），表保留只为不丢老数据；
        -- 备份是整库导出，老案件数据仍在里面。
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            town TEXT NOT NULL,
            case_no TEXT NOT NULL DEFAULT '',
            case_name TEXT NOT NULL,
            progress TEXT NOT NULL DEFAULT 'investigating',
            fine_amount REAL NOT NULL DEFAULT 0,
            reporter TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT NOT NULL,
            community TEXT NOT NULL,
            phone TEXT DEFAULT '',
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending / done
            reporter TEXT NOT NULL,
            handler TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            handled_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        # 旧库迁移：records 缺列时补上
        cols = [r[1] for r in db.execute("PRAGMA table_info(records)").fetchall()]
        for col in ("deadline", "lead_dept", "assist_dept", "found_at"):
            if col not in cols:
                db.execute(
                    "ALTER TABLE records ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % col)
                print("[migrate] records 增加 %s 列" % col)
        # 发现日期（实际巡查/发现那天，可手填；补录时能改成真实日期）。
        # 老数据没有这一列，用录入日期回填，之后再编辑修正。
        db.execute(
            "UPDATE records SET found_at = substr(created_at, 1, 10) "
            "WHERE found_at IS NULL OR found_at = ''")
        db.commit()
        ccols = [r[1] for r in db.execute("PRAGMA table_info(cases)").fetchall()]
        if "case_no" not in ccols:
            db.execute(
                "ALTER TABLE cases ADD COLUMN case_no TEXT NOT NULL DEFAULT ''")
            print("[migrate] cases 增加 case_no 列")
        ucols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
        if "title" not in ucols:
            db.execute(
                "ALTER TABLE users ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            print("[migrate] users 增加 title 列")
        # 组织架构改版（2026-09-06）：去掉中队/办公室，记录只按两个乡镇归类
        if db.execute(
                "SELECT COUNT(*) c FROM sqlite_master "
                "WHERE type='table' AND name='units'").fetchone()["c"]:
            db.execute("DROP TABLE units")
            print("[migrate] 已删除 units 表（中队/办公室体系）")
        _to_town = (
            "CASE WHEN {c} LIKE '鄱阳镇%%' THEN '鄱阳镇' "
            "WHEN {c} LIKE '饶州街道%%' THEN '饶州街道' ELSE {c} END"
        )
        for _tbl in ("records", "cases"):
            _names = [r[1] for r in db.execute(
                "PRAGMA table_info(%s)" % _tbl).fetchall()]
            if "team" in _names:
                db.execute("ALTER TABLE %s RENAME COLUMN team TO town" % _tbl)
                db.execute("UPDATE %s SET town = %s" % (
                    _tbl, _to_town.format(c="town")))
                print("[migrate] %s.team → %s.town（按前缀归到两个乡镇）"
                      % (_tbl, _tbl))
        # 投诉并入巡查（2026-09-02 拍板）：complaints 迁移为 records（分类「投诉纠纷」）
        def _thumb_rel(rel):
            stem, ext = rel.rsplit(".", 1)
            return f"{stem}_thumb.{ext}"

        for c in db.execute("SELECT * FROM complaints").fetchall():
            desc = (c["content"] or "").strip()
            if c["phone"]:
                desc = f"{desc}（来电：{c['phone']}）" if desc else f"来电：{c['phone']}"
            status = "closed" if c["status"] == "done" else "pending"
            result = ""
            if c["status"] == "done":
                result = "已处理"
                if c["handler"]:
                    result += f"（处理人：{c['handler']}）"
            cur = db.execute(
                "INSERT INTO records(town, community, category, description, "
                "status, result, deadline, reporter, created_at, closed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (town_of(c["team"]), c["community"] or "", "投诉纠纷", desc,
                 status, result, "", c["reporter"] or "", c["created_at"],
                 c["handled_at"] if c["status"] == "done" else None),
            )
            rid = cur.lastrowid
            for im in db.execute(
                "SELECT * FROM images WHERE record_id=? AND type='complaint'",
                (c["id"],),
            ).fetchall():
                old_rel = im["filepath"]
                old_p = UPLOADS_DIR / old_rel
                new_rel = f"records/{rid}/before/{Path(old_rel).name}"
                new_p = UPLOADS_DIR / new_rel
                moved = False
                if old_p.exists():
                    new_p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old_p), str(new_p))
                    moved = True
                old_t = UPLOADS_DIR / _thumb_rel(old_rel)
                if old_t.exists():
                    new_t = UPLOADS_DIR / _thumb_rel(new_rel)
                    shutil.move(str(old_t), str(new_t))
                if moved:
                    db.execute(
                        "UPDATE images SET record_id=?, type='before', "
                        "filepath=? WHERE id=?",
                        (rid, new_rel, im["id"]))
                else:
                    db.execute("DELETE FROM images WHERE id=?", (im["id"],))
            db.execute("DELETE FROM complaints WHERE id=?", (c["id"],))
            print(f"[migrate] 投诉#{c['id']} → 巡查记录#{rid}（投诉纠纷）")
        # 单账号：库里只保留一个主账号
        if db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
            db.execute(
                "INSERT INTO users(name, unit, role, pin, created_at) "
                "VALUES(?,?,?,?,?)",
                ("主账号", "", "super", OWNER_PIN, now()),
            )
            print(f"[init] 已创建唯一主账号：主账号，初始密码={OWNER_PIN}"
                  "（可在「修改密码」页更改）")
        elif db.execute(
                "SELECT COUNT(*) c FROM users").fetchone()["c"] > 1:
            db.execute("DELETE FROM users WHERE id NOT IN "
                       "(SELECT id FROM users ORDER BY id LIMIT 1)")
            db.execute("UPDATE users SET unit='', role='super', name='主账号'")
            print("[migrate] 已收敛为单账号：删除其余账号，保留最早建的那个")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today_str() -> str:
    """今天 YYYY-MM-DD：新建 / 销号表单里日期输入框的默认值。"""
    return date.today().isoformat()


init_db()


# ---------- 访问控制（单账号：登录即可，无角色范围） ----------
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def require_user(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ---------- 图片 ----------
def save_photos(files, subdir):
    base = UPLOADS_DIR / subdir
    base.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        if not f or not f.filename:
            continue
        uid = uuid.uuid4().hex
        orig = f"{uid}.jpg"
        thumb = f"{uid}_thumb.jpg"
        try:
            image_utils.process(f, base / orig, base / thumb)
        except ValueError:
            continue
        saved.append((f"{subdir}/{orig}", f"{subdir}/{thumb}"))
    return saved


def thumb_of(filepath):
    stem, ext = filepath.rsplit(".", 1)
    return f"{stem}_thumb.{ext}"


def community_autocomplete(q, limit=8):
    db = get_db()
    if q:
        rows = db.execute(
            "SELECT name FROM communities WHERE name LIKE ? ORDER BY count DESC LIMIT ?",
            (f"%{q}%", limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT name FROM communities ORDER BY count DESC LIMIT ?", (limit,)
        ).fetchall()
    return [r["name"] for r in rows]


def bump_community(name):
    db = get_db()
    db.execute(
        "INSERT INTO communities(name, count) VALUES(?, 1) "
        "ON CONFLICT(name) DO UPDATE SET count = count + 1",
        (name,),
    )
    db.commit()


# ---------- 登录（单账号 4 位密码） ----------
# 防爆破：同一 IP 5 分钟内失败 10 次即锁定。4 位 PIN 只有 1 万种组合，
# 不加这层的话一个脚本几秒就能穷举完。
LOGIN_WINDOW = 300        # 秒
LOGIN_MAX_FAILS = 10
_login_fails = {}         # {client_ip: (首次失败时间戳, 失败次数)}


def _bump_fail():
    ip = request.remote_addr or "?"
    now = datetime.now().timestamp()
    t0, n = _login_fails.get(ip, (0.0, 0))
    if now - t0 > LOGIN_WINDOW:
        n = 0
    _login_fails[ip] = (now, n + 1)


def _fails_locked():
    ip = request.remote_addr or "?"
    t0, n = _login_fails.get(ip, (0.0, 0))
    if datetime.now().timestamp() - t0 > LOGIN_WINDOW:
        return False
    return n >= LOGIN_MAX_FAILS


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if _fails_locked():
            return render_template(
                "login.html",
                error="尝试太频繁，%d 分钟后再试" % (LOGIN_WINDOW // 60)), 429
        pin = (request.form.get("pin") or "").strip()
        row = get_db().execute("SELECT * FROM users LIMIT 1").fetchone()
        if row and row["pin"] == pin:
            _login_fails.pop(request.remote_addr or "?", None)
            session.permanent = True
            session["uid"] = row["id"]
            log_action("登录", "主账号登录")
            return redirect(url_for("index"))
        _bump_fail()
        # 登录失败走 PRG：重定向到干净地址，避免链接带参循环自动提交
        flash("密码不对，请重新输入", "error")
        return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("uid", None)
    return redirect(url_for("login"))


# ---------- 修改自己的密码（所有人） ----------
@app.route("/password", methods=["GET", "POST"])
@require_user
def password():
    u = current_user()
    if request.method == "POST":
        cur = (request.form.get("current") or "").strip()
        new = (request.form.get("new") or "").strip()
        confirm = (request.form.get("confirm") or "").strip()
        if u["pin"] != cur:
            return render_template("password.html", error="当前密码不对")
        if len(new) != 4 or not new.isdigit():
            return render_template("password.html", error="新密码必须是 4 位数字")
        if new != confirm:
            return render_template("password.html", error="两次输入的新密码不一致")
        get_db().execute("UPDATE users SET pin=? WHERE id=?", (new, u["id"]))
        get_db().commit()
        log_action("修改密码", "修改自己的 4 位密码")
        flash("密码修改成功", "ok")
        return redirect(url_for("index"))
    return render_template("password.html", error=None)


# ---------- 巡查记录 ----------
# 记录按哪个时间划进时间段：create 录入时间；smart 智能（已结案按结案时间、
# 未结案按录入时间）；close 结案时间。
TIME_KEYS = {
    "found": "found_at",
    "create": "created_at",
    "smart": ("CASE WHEN status = 'closed' AND closed_at IS NOT NULL "
              "AND closed_at <> '' THEN closed_at ELSE created_at END"),
    "close": "closed_at",
}


def time_key(basis="create"):
    """basis → records 的时间列 SQL 片段。闭集字典，不做任何插值。"""
    return TIME_KEYS.get(basis, TIME_KEYS["create"])


def _basis(default="create"):
    """从查询串取统计口径；非法值回落 default。"""
    b = (request.args.get("basis") or "").strip()
    return b if b in TIME_KEYS else default


def _record_where(basis="create"):
    """按查询串拼 records 的筛选条件，返回 (where 子句, 参数列表)。

    basis 决定时间条件落在哪一列；默认 create，渲染出的 SQL 与旧版逐字相同。
    """
    col = time_key(basis)
    town = (request.args.get("town") or request.args.get("team") or "").strip()
    where, params = "", []
    if town in TOWNS:
        where, params = "town = ?", [town]
    for cond, val in (
        ("category", request.args.get("category", "")),
        ("reporter", request.args.get("reporter", "")),
        ("community", request.args.get("community", "")),
    ):
        val = (val or "").strip()
        if val:
            where = (where + " AND " if where else "") + cond + " = ?"
            params.append(val)
    status = request.args.get("status", "")
    if status in ("pending", "closed"):
        where = (where + " AND " if where else "") + "status = ?"
        params.append(status)
    # 统一按「年月日」比较：created_at / closed_at 带时分秒，found_at 只有日期，
    # 直接比整串会把当天（或只填了日期的销号记录）漏掉。
    dcol = "substr(%s, 1, 10)" % col
    month = (request.args.get("month") or "").strip()
    if month:
        where = (where + " AND " if where else "") + dcol + " LIKE ?"
        params.append(month + "%")
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    if start:
        where = (where + " AND " if where else "") + dcol + " >= ?"
        params.append(start)
    if end:
        where = (where + " AND " if where else "") + dcol + " <= ?"
        params.append(end)
    q = (request.args.get("q") or "").strip()
    if q:
        where = (where + " AND " if where else "") + \
            "(community LIKE ? OR description LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    return where, params


def _decorate(records):
    """补列表页要用的缩略图和待处理天数。"""
    today = datetime.now().date()
    for r in records:
        img = get_db().execute(
            "SELECT filepath FROM images WHERE record_id=? AND type='before' "
            "ORDER BY id LIMIT 1", (r["id"],)
        ).fetchone()
        r["thumb"] = thumb_of(img["filepath"]) if img else None
        if r["status"] == "pending":
            try:
                # 未处理天数按发现日算，补录的记录才不会算少
                base = (r["found_at"] or r["created_at"][:10])[:10]
                d = datetime.strptime(base, "%Y-%m-%d").date()
                r["days_pending"] = (today - d).days
            except ValueError:
                r["days_pending"] = 0
        else:
            r["days_pending"] = None
    return records


@app.route("/")
@require_user
def index():
    basis = _basis("create")
    where, params = _record_where(basis)

    sql = "SELECT * FROM records"
    if where:
        sql += " WHERE " + where
    # 按发现时间倒序（补录的记录按真实发现日排），同日再按录入先后
    sql += " ORDER BY COALESCE(NULLIF(found_at, ''), substr(created_at,1,10)) DESC, id DESC"
    records = _decorate([dict(r) for r in get_db().execute(sql, params).fetchall()])

    this_month = datetime.now().strftime("%Y-%m")

    def count_where(extra="", extra_params=()):
        """在当前筛选条件上再叠加一个条件计数。"""
        if where and extra:
            w = where + " AND " + extra
        else:
            w = where or extra
        sql2 = "SELECT COUNT(*) c FROM records"
        if w:
            sql2 += " WHERE " + w
        return get_db().execute(sql2, params + list(extra_params)).fetchone()["c"]

    stats = {
        "month_new": count_where("created_at LIKE ?", (this_month + "%",)),
        "pending": count_where("status='pending'"),
        "closed": count_where("status='closed'"),
    }

    # 小区候选：本库出现过的，按出现次数排
    community_options = [r["name"] for r in get_db().execute(
        "SELECT name FROM communities ORDER BY count DESC, name").fetchall()]

    # 筛选摘要：从统计页带参数跳过来时，让用户看得到也能一键清掉
    scope = []
    _start = (request.args.get("start") or "").strip()
    _end = (request.args.get("end") or "").strip()
    if _start and _end:
        scope.append(f"{_start} 至 {_end}")
    elif _start or _end:
        scope.append(_start or _end)
    if basis != "create":
        scope.append(BASIS_LABELS[basis])
    if request.args.get("month"):
        scope.append(request.args.get("month"))
    scope_note = " · ".join(scope)

    return render_template(
        "index.html", records=records, stats=stats, user=current_user(),
        categories=CATEGORIES,
        community_options=community_options,
        scope_note=scope_note,
        sel={"town": request.args.get("town") or request.args.get("team") or "",
             "category": request.args.get("category", ""),
             "reporter": request.args.get("reporter", ""),
             "status": request.args.get("status", ""),
             "month": request.args.get("month", ""),
             "community": request.args.get("community", ""),
             "q": request.args.get("q", ""),
             "start": _start, "end": _end, "basis": basis},
        this_month=this_month,
    )


@app.route("/create", methods=["GET", "POST"])
@require_user
def create():
    if request.method == "POST":
        town = (request.form.get("town") or "").strip()
        community = (request.form.get("community") or "").strip()
        category = (request.form.get("category") or "").strip() or "其他"
        description = (request.form.get("description") or "").strip()
        deadline = (request.form.get("deadline") or "").strip()
        lead_dept = (request.form.get("lead_dept") or "").strip()
        assist_dept = (request.form.get("assist_dept") or "").strip()
        # 发现日期：默认今天，补录时可改成实际巡查那天
        found_at = (request.form.get("found_at") or "").strip() or today_str()
        if town not in TOWNS:
            return render_template("create.html", error="请选择所属乡镇",
                                   categories=CATEGORIES,
                                   today=today_str()), 400
        # 小区、分类、描述都可以留空（分类缺省记"其他"），之后可在详情页编辑补全
        db = get_db()
        cur = db.execute(
            "INSERT INTO records(town, community, category, description, "
            "status, deadline, lead_dept, assist_dept, reporter, created_at, "
            "found_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (town, community, category, description, "pending", deadline,
             lead_dept, assist_dept, current_user()["name"], now(), found_at),
        )
        rid = cur.lastrowid
        saved = save_photos(request.files.getlist("photos"),
                            f"records/{rid}/before")
        db.executemany(
            "INSERT INTO images(record_id, type, filepath, created_at) "
            "VALUES(?,?,?,?)",
            [(rid, "before", p[0], now()) for p in saved],
        )
        db.commit()
        log_action("新增巡查记录",
                   f"{town} · {community or '未填小区'} · {category}")
        bump_community(community)
        return redirect(url_for("detail", rid=rid))
    return render_template(
        "create.html", error=None, categories=CATEGORIES, today=today_str(),
        tpl=DESCRIPTION_TEMPLATES, phrases=RESULT_PHRASES,
        lead_default=get_setting("ledger_lead_dept", "县城市管理综合行政执法大队"),
        assist_default=get_setting("ledger_assist_dept", "社区、物业"))


@app.route("/record/<int:rid>")
@require_user
def detail(rid):
    r = get_db().execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404)
    images = get_db().execute(
        "SELECT * FROM images WHERE record_id=? ORDER BY id", (rid,)
    ).fetchall()
    before = [dict(i) for i in images if i["type"] == "before"]
    after = [dict(i) for i in images if i["type"] == "after"]
    return render_template("detail.html", r=r, before=before, after=after,
                           user=current_user())


@app.route("/record/<int:rid>/edit", methods=["GET", "POST"])
@require_user
def edit_record(rid):
    r = get_db().execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404)
    if request.method == "POST":
        town = (request.form.get("town") or "").strip()
        community = (request.form.get("community") or "").strip()
        category = (request.form.get("category") or "").strip() or "其他"
        description = (request.form.get("description") or "").strip()
        deadline = (request.form.get("deadline") or "").strip()
        lead_dept = (request.form.get("lead_dept") or "").strip()
        assist_dept = (request.form.get("assist_dept") or "").strip()
        # 发现日期可改：补录的记录在这里改成实际巡查那天
        found_at = (request.form.get("found_at") or "").strip() or \
            (r["found_at"] or r["created_at"][:10])
        if town not in TOWNS:
            return render_template("edit.html", r=r, categories=CATEGORIES,
                                   error="请选择所属乡镇"), 400
        get_db().execute(
            "UPDATE records SET town=?, community=?, category=?, description=?, "
            "deadline=?, lead_dept=?, assist_dept=?, found_at=? WHERE id=?",
            (town, community, category, description, deadline, lead_dept,
             assist_dept, found_at, rid),
        )
        # 已销号的记录：销号日期填错了可以在这里改回来
        if r["status"] == "closed":
            closed_at = (request.form.get("closed_at") or "").strip()
            if closed_at:
                get_db().execute(
                    "UPDATE records SET closed_at=? WHERE id=?", (closed_at, rid))
        # 编辑时补拍/补充的现场照片，并入「整改前」照片
        saved = save_photos(request.files.getlist("photos"),
                            f"records/{rid}/before")
        if saved:
            get_db().executemany(
                "INSERT INTO images(record_id, type, filepath, created_at) "
                "VALUES(?,?,?,?)",
                [(rid, "before", p2[0], now()) for p2 in saved],
            )
        get_db().commit()
        log_action("编辑巡查记录", f"记录#{rid} {community or '未填小区'} · {category}")
        bump_community(community)
        return redirect(url_for("detail", rid=rid))
    return render_template(
        "edit.html", r=r, categories=CATEGORIES,
        tpl=DESCRIPTION_TEMPLATES, phrases=RESULT_PHRASES,
        lead_default=get_setting("ledger_lead_dept", "县城市管理综合行政执法大队"),
        assist_default=get_setting("ledger_assist_dept", "社区、物业"))


@app.route("/record/<int:rid>/delete", methods=["POST"])
@require_user
def delete_record(rid):
    r = get_db().execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404)
    images = get_db().execute(
        "SELECT filepath FROM images WHERE record_id=?", (rid,)
    ).fetchall()
    db = get_db()
    db.execute("DELETE FROM images WHERE record_id=?", (rid,))
    db.execute("DELETE FROM records WHERE id=?", (rid,))
    db.commit()
    log_action("删除巡查记录",
               f"记录#{rid} {r['community'] or '未填小区'} · {r['category']}")
    for img in images:
        p = UPLOADS_DIR / img["filepath"]
        try:
            if p.exists():
                p.unlink()
            t = UPLOADS_DIR / thumb_of(img["filepath"])
            if t.exists():
                t.unlink()
        except OSError:
            pass
    # 顺手清掉变空的照片目录，否则备份会把空壳/孤文件一直打包进去
    for d in {UPLOADS_DIR / f"records/{rid}/before", UPLOADS_DIR / f"records/{rid}/after"}:
        try:
            d.rmdir()                      # 只有空目录才成功
            (d.parent).rmdir()             # records/<rid>
            UPLOADS_DIR.rmdir()            # uploads
        except OSError:
            pass
    flash("记录已删除", "ok")
    return redirect(url_for("index"))


@app.route("/record/<int:rid>/close", methods=["GET", "POST"])
@require_user
def close(rid):
    r = get_db().execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404)
    if r["status"] == "closed":
        return redirect(url_for("detail", rid=rid))
    if request.method == "POST":
        result = (request.form.get("result") or "").strip()
        if not result:
            return render_template("close.html", r=r, error="处理结果要填")
        deadline = (request.form.get("deadline") or "").strip() or r["deadline"]
        db = get_db()
        saved = save_photos(request.files.getlist("photos"),
                            f"records/{rid}/after")
        db.executemany(
            "INSERT INTO images(record_id, type, filepath, created_at) "
            "VALUES(?,?,?,?)",
            [(rid, "after", p[0], now()) for p in saved],
        )
        # 销号日期可填：默认今天，补录/隔天销号能改成真实日期
        closed_at = (request.form.get("closed_at") or "").strip() or now()
        db.execute(
            "UPDATE records SET status='closed', result=?, closed_at=?, "
            "deadline=? WHERE id=?", (result, closed_at, deadline, rid),
        )
        db.commit()
        log_action("整改销号", f"记录#{rid} {r['community'] or '未填小区'} · {r['category']}")
        return redirect(url_for("detail", rid=rid))
    return render_template("close.html", r=r, error=None, today=today_str(),
                           phrases=RESULT_PHRASES)


# ---------- 统计 ----------
# 时间口径：create 按录入时间；smart 智能（已结案按结案时间、未结案按录入时间）；
# close 按结案时间。默认 smart —— 一条记录算在它该算的时段里。
BASIS_LABELS = {
    "found": "按发现时间",
    "create": "按录入时间",
    "smart": "智能口径",
    "close": "按结案时间",
}
BASIS_HINTS = {
    "found": "按实际发现那天统计，补录的记录算在真实发现日。",
    "create": "按记录建立时间统计，跨期才办结的也算在本期。",
    "smart": "已结案按结案时间、未结案按录入时间。",
    "close": "只看这段时间内结案的记录，未结案不计入。",
}


@app.route("/stats")
@require_user
def stats():
    basis = _basis("smart")
    col = time_key(basis)
    town = (request.args.get("town") or request.args.get("team") or "").strip()
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    if not start and not end:
        # 默认本月 1 号到今天
        start = datetime.now().replace(day=1).strftime("%Y-%m-01")
        end = datetime.now().strftime("%Y-%m-%d")

    cond, params = [], []
    if town in TOWNS:
        cond.append("town = ?")
        params.append(town)
    dcol = "substr(%s, 1, 10)" % col   # 同 _record_where：统一按年月日比较
    if start:
        cond.append(dcol + " >= ?")
        params.append(start)
    if end:
        cond.append(dcol + " <= ?")
        params.append(end)
    scope = " AND ".join(cond)
    db = get_db()

    total = db.execute(
        "SELECT COUNT(*) c FROM records WHERE " + scope, params).fetchone()["c"]
    closed = db.execute(
        "SELECT COUNT(*) c FROM records WHERE " + scope + " AND status='closed'",
        params).fetchone()["c"]
    by_town = db.execute(
        "SELECT town, COUNT(*) c, SUM(status='closed') closed FROM records "
        "WHERE " + scope + " GROUP BY town", params).fetchall()
    by_category = db.execute(
        "SELECT category, COUNT(*) c FROM records WHERE " + scope +
        " GROUP BY category ORDER BY c DESC", params).fetchall()
    by_community = db.execute(
        "SELECT community, COUNT(*) c FROM records WHERE " + scope +
        " GROUP BY community ORDER BY c DESC LIMIT 10", params).fetchall()

    # 结案口径没有办结率（分母分子同一批，恒 100%），换成未结积压和平均办理天数
    backlog, avg_days = 0, 0
    if basis == "close":
        bcond = "status != 'closed'"
        bparams = []
        if town in TOWNS:
            bcond += " AND town = ?"
            bparams.append(town)
        if end:
            bcond += " AND created_at <= ?"
            bparams.append(end + " 23:59")
        backlog = db.execute(
            "SELECT COUNT(*) c FROM records WHERE " + bcond, bparams).fetchone()["c"]
        avg_days = db.execute(
            "SELECT COALESCE(ROUND(AVG(julianday(closed_at) - julianday(created_at)), 1), 0) d "
            "FROM records WHERE " + scope + " AND status='closed'", params,
        ).fetchone()["d"]

    if basis == "close":
        labels = ("本期结案", "截至未结", "平均办理天数")
    elif basis == "smart":
        labels = ("本期涉及", "其中已办结", "办结率")
    else:
        labels = ("本期发现", "其中已办结", "办结率")

    return render_template(
        "stats.html", basis=basis, basis_labels=BASIS_LABELS, labels=labels,
        hint=BASIS_HINTS[basis], start=start, end=end,
        total=total, closed=closed,
        rate=round(closed / total * 100, 1) if total else 0,
        backlog=backlog, avg_days=avg_days,
        by_town=by_town, by_category=by_category, by_community=by_community,
        sel={"town": town if town in TOWNS else ""},
        q={"start": start, "end": end, "basis": basis},
    )



# ---------- 导出筛选参数（通用） ----------
def _export_basis():
    """台账按哪个时间筛：found 发现时间 / create 录入时间，默认 create。"""
    b = (request.args.get("basis") or "").strip()
    return b if b in ("found", "create") else "create"


def _export_filters():
    """台账导出的筛选参数，返回 (where, params, start, end, month)。

    口径只认 found / create 两个，非法值回落 create；下载链接必须带上同一个
    basis（见 ledger.html），否则预览和下载的范围会不一致。
    start/end 已含在 where 里，单独返回是为了给模板回填日期框、拼下载链接。
    """
    where, params = _record_where(_export_basis())
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    month = request.args.get("month", "")
    return where, params, start, end, month


# ---------- 小区摸排台账导出（预览页 + Excel） ----------
LEDGER_DEPS = [
    ("ledger_report_dept", "问题上报部门", "县城管局"),
    ("ledger_lead_dept", "牵头部门", "县城市管理综合行政执法大队"),
    ("ledger_assist_dept", "配合部门", "社区、物业"),
]

# 一页表格放多少行数据：照原表（标题 35.25pt + 表头 35pt + 9×45pt = 475.25pt，
# A4 横向去掉上下页边距可用约 487pt）。行数超了就再起一页表格，标题和表头重复。
LEDGER_ROWS_PER_PAGE = 9


def _ledger_pages(rows, per_page=LEDGER_ROWS_PER_PAGE):
    """把一个小区的行按每页固定行数切开：每页 = 标题 + 表头 + per_page 行（不足补带边框空行）。

    必须按页切开、每页都固定行数，否则一个小区行数超过一页时，
    Excel 自动分页会把表格和它下面的照片页推错位，后面所有页码跟着全乱。
    """
    if not rows:
        return [{"rows": [], "pad": per_page}]
    out = []
    for i in range(0, len(rows), per_page):
        chunk = rows[i:i + per_page]
        out.append({"rows": chunk, "pad": per_page - len(chunk)})
    return out


def _ledger_groups_from(where, params):
    """按给定 SQL 条件取巡查记录并按小区分组；居民投诉并入同小区表（类目「居民投诉」共用序号）。"""
    sql = "SELECT * FROM records"
    if where:
        sql += " WHERE " + where
    records = [dict(r) for r in get_db().execute(sql, params).fetchall()]

    order = {c: i for i, c in enumerate(CATEGORIES)}
    by_comm = {}
    for r in records:
        by_comm.setdefault(r["community"] or "未填小区", []).append(r)

    def _comm_town(recs):
        """一个小区只认一个乡镇：按记录条数的多数票定，票数相同取最近一条（id 最大）。

        同一小区被填成两个乡镇时（历史数据难免），必须只归一个乡镇，
        否则排序用的乡镇和表头显示的乡镇会取自不同记录，导出里同一个小区
        会被拆成两块（饶州街道 → 鄱阳镇 → 饶州街道 → 鄱阳镇）。
        """
        cnt, last = {}, {}
        for r in recs:
            t = (r.get("town") or "").strip()
            cnt[t] = cnt.get(t, 0) + 1
            last[t] = max(last.get(t, -1), r["id"])
        return max(cnt, key=lambda t: (cnt[t], last[t]))

    def _town_rank(t):
        """排序用的乡镇序号：饶州街道 0、鄱阳镇 1，认不出的排最后。"""
        return TOWNS.index(t) if t in TOWNS else len(TOWNS)

    # 小区排序：先按乡镇（饶州街道在上、鄱阳镇在下），同乡镇内再按小区名
    def _group_key(item):
        comm, recs = item
        return (_town_rank(_comm_town(recs)), comm)

    groups = []
    for comm, _recs in sorted(by_comm.items(), key=_group_key):
        town = _comm_town(_recs)   # 排序与表头用同一个值，不再各取一条记录
        recs = sorted(by_comm.get(comm, []),
                      key=lambda r: (order.get(r["category"], 99), r["id"]))
        rows = []
        num, last_cat = 0, None
        blocks = []  # 每组照片一块：共用该记录分类的序号
        for r in recs:
            if r["category"] != last_cat:
                num += 1
                last_cat = r["category"]
            r["_status_text"] = "已整改" if r["status"] == "closed" else "未整改"
            r["_remark"] = ""
            rows.append((num, r))
            b, a = [], []
            for im in get_db().execute(
                "SELECT * FROM images WHERE record_id=? ORDER BY id",
                (r["id"],),
            ).fetchall():
                (b if im["type"] == "before" else a).append(im["filepath"])
            if b or a:
                blocks.append({"num": num, "before": b, "after": a,
                               "before_thumbs": [thumb_of(p) for p in b],
                               "after_thumbs": [thumb_of(p) for p in a]})
        groups.append({
            "community": comm, "rows": rows,
            "pages": _ledger_pages(rows),   # 每页固定 9 行：表格页与照片页才对得上
            "town": town,
            "blocks": blocks,
        })
    return groups


def _ledger_groups():
    """摸排台账数据：按小区分组，每小区返回序号共用行 + 整改前/后照片路径。"""
    where, params, start, end, month = _export_filters()
    return _ledger_groups_from(where, params), start, end


@app.route("/export/ledger")
@require_user
def export_ledger():
    groups, start, end = _ledger_groups()
    deps = {k: get_setting(k, d) or d for k, _l, d in LEDGER_DEPS}
    # 当前筛选范围内出现过的小区，供「打印特定小区」下拉
    where, params = _record_where(_export_basis())
    sql = "SELECT DISTINCT community FROM records WHERE community != ''"
    if where:
        sql += " AND " + where
    cnames = {r["community"] for r in get_db().execute(sql, params).fetchall()}
    return render_template(
        "ledger.html", groups=groups, deps=deps,
        start=start, end=end, basis=_export_basis(),
        sel={"town": request.args.get("town") or request.args.get("team") or "",
             "community": request.args.get("community", ""),
             "category": request.args.get("category", "")},
        categories=CATEGORIES,
        community_options=sorted(cnames))


@app.route("/export/ledger/settings", methods=["POST"])
@require_user
def ledger_settings():
    vals = []
    for key, _label, default in LEDGER_DEPS:
        val = (request.form.get(key) or "").strip() or default
        set_setting(key, val)
        vals.append(val)
    log_action("修改摸排台账表头", " / ".join(vals))
    flash("台账表头已保存", "ok")
    return redirect(url_for(
        "export_ledger",
        start=request.args.get("start", ""), end=request.args.get("end", ""),
        town=request.args.get("town", ""), team=request.args.get("team", ""),
        community=request.args.get("community", ""),
        category=request.args.get("category", ""),
        basis=request.args.get("basis", "")))


def _ledger_workbook(groups):
    """生成摸排台账工作簿：每页固定行数、页脚与原表一致；照片用 WPS 嵌入单元格（DISPIMG）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.pagebreak import Break
    from PIL import Image as PILImage

    deps = [get_setting(k, d) or d for k, _l, d in LEDGER_DEPS]
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    # 字体仿原表：标题黑体28、表头黑体（序号/备注16，其余12）、正文宋体11
    title_font = Font(name="黑体", size=28)
    title_align = Alignment(horizontal="centerContinuous", vertical="center",
                            wrap_text=True)
    head_big = Font(name="黑体", size=16)
    head_small = Font(name="黑体", size=12)
    tno_font = Font(name="宋体", size=10.5)   # 表格序号行
    label_font = Font(name="宋体", size=24)   # 整改前/整改后标签

    headers = ["序号", "问题上报部门", "牵头部门", "配合部门",
               "问题描述", "整改举措", "整改时限", "整改情况", "备注"]
    widths = [7.375, 14.625, 21.125, 14.375, 20.75, 21.125, 14.625, 13.125, 9.125]
    def embed_photo(filepath):
        """直接取原图（不缩放、不垫白底），返回 (bytes, 宽, 高)。"""
        p = UPLOADS_DIR / filepath
        if not p.exists():
            return None
        try:
            with PILImage.open(p) as im:
                w, h = im.size
            return p.read_bytes(), w, h
        except Exception:
            return None

    wb = Workbook()
    ws = wb.active
    ws.title = "小区摸排台账"
    # 打开即分页预览：每页中央自动显示「第1页 / 第2页…」水印，与原表一致
    ws.sheet_view.view = "pageBreakPreview"
    ws.sheet_view.tabSelected = True
    ws.sheet_view.zoomScale = 115
    ws.sheet_view.zoomScaleNormal = 85
    # 纸张：A4 横向 + 原表页边距
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = 9
    ws.page_margins.left = ws.page_margins.right = 0.590277777777778
    ws.page_margins.top = ws.page_margins.bottom = 0.751388888888889
    ws.page_margins.header = ws.page_margins.footer = 0.298611111111111
    # 页脚与原表一致：居中「第 &P 页」
    # ⚠ 不要再设 oddFooter.center.size：openpyxl 会把字号序列化成页脚控制码「&9」，
    #   写成「&C&9 第 &P 页」，而原表是「&C第 &P 页」，两者不一致。
    #   注：打印时看到的「第1页/第2页」大字来自上面的 pageBreakPreview 水印，不是页脚。
    ws.oddFooter.center.text = "第 &P 页"
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.column_dimensions["J"].width = 9.64  # 原表右侧余量列
    if not groups:  # 无数据时也要有可见内容
        ws["A1"] = "当前范围内没有巡查记录"
        ws["A1"].font = title_font
    # 逐小区排版：每个小区按「每页固定 9 行」切页，每页表格/每行照片后打手动分页符
    # （和原表一致）。行数超过一页就再起一页表格，标题表头重复，绝不把表格和照片挤乱。
    placements = []  # 嵌入单元格图片 {ref, disp, x, y, cx, cy, png}
    y_pt = 0.0       # 当前行顶距表顶的点数（算图片 y 偏移）
    r = 1
    for g in groups:
        for pg in g["pages"]:
            # 标题行（黑体28 居中）
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=9)
            c = ws.cell(row=r, column=1, value=f"{g['community']}小区摸排情况")
            c.font = title_font
            c.alignment = title_align
            ws.row_dimensions[r].height = 35.25
            r += 1
            y_pt += 35.25
            # 表头（序号/备注黑体16，其余黑体12）
            for col, h in enumerate(headers, 1):
                cell = ws.cell(row=r, column=col, value=h)
                cell.font = head_big if col in (1, 9) else head_small
                cell.border = border
                cell.alignment = center
            ws.row_dimensions[r].height = 35
            r += 1
            y_pt += 35
            # 数据行 + 补足本页固定行数（带边框空行）
            for num, rec in pg["rows"]:
                vals = [num, deps[0],
                        rec.get("lead_dept") or deps[1],
                        rec.get("assist_dept") or deps[2],
                        rec["description"] or rec["category"],
                        rec["result"] or "",
                        rec["deadline"] or "",
                        rec["_status_text"],
                        rec["_remark"] or ""]
                for col, v in enumerate(vals, 1):
                    cell = ws.cell(row=r, column=col, value=v)
                    cell.border = border
                    cell.font = Font(name="宋体", size=10 if col == 5 else 11)
                    cell.alignment = center
                ws.row_dimensions[r].height = 45
                r += 1
                y_pt += 45
            for _ in range(pg["pad"]):
                for col in range(1, 10):
                    ws.cell(row=r, column=col).border = border
                ws.row_dimensions[r].height = 45
                r += 1
                y_pt += 45
            ws.row_breaks.append(Break(id=r - 1))   # 这一页表格到此为止
        # 每组照片一块：表格序号（共用分类序号）+ 整改前/整改后标签 + 照片
        for blk in g["blocks"]:
            tno = ws.cell(row=r, column=1,
                          value=f"{g['community']}表格序号{blk['num']}")
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
            tno.font = tno_font
            tno.alignment = center
            ws.row_dimensions[r].height = 45
            r += 1
            y_pt += 45
            lab = ws.cell(row=r, column=1, value="整改前")
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
            lab2 = ws.cell(row=r, column=6, value="整改后")
            ws.merge_cells(start_row=r, start_column=6, end_row=r, end_column=9)
            lab.font = lab2.font = label_font
            lab.alignment = lab2.alignment = center
            ws.row_dimensions[r].height = 45
            r += 1
            y_pt += 45
            for i in range(max(len(blk["before"]), len(blk["after"]))):
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
                ws.merge_cells(start_row=r, start_column=6, end_row=r, end_column=9)
                n = len(placements) + 1
                if i < len(blk["before"]):
                    res = embed_photo(blk["before"][i])
                    if res:
                        data, w, h = res
                        disp = f"ID_cg{n}"
                        placements.append({
                            "ref": f"A{r}", "disp": disp,
                            "x": 0, "y": int(round(y_pt * 12700)),
                            "cx": w * 9525, "cy": h * 9525,
                            "img": data,
                        })
                        ws.cell(row=r, column=1).value = f'=_xlfn.DISPIMG("{disp}",1)'
                        n += 1
                if i < len(blk["after"]):
                    res = embed_photo(blk["after"][i])
                    if res:
                        data, w, h = res
                        disp = f"ID_cg{n}"
                        placements.append({
                            "ref": f"F{r}", "disp": disp,
                            "x": 5962650, "y": int(round(y_pt * 12700)),
                            "cx": w * 9525, "cy": h * 9525,
                            "img": data,
                        })
                        ws.cell(row=r, column=6).value = f'=_xlfn.DISPIMG("{disp}",1)'
                ws.row_dimensions[r].height = 368.5
                r += 1
                y_pt += 368.5
                ws.row_breaks.append(Break(id=r - 1))   # 一行照片一页
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    if placements:
        buf = _inject_cellimages(buf, placements)
    return buf


def _inject_cellimages(buf, placements):
    """向 xlsx 注入 WPS「嵌入单元格」图片：cellimages.xml + DISPIMG 公式缓存值 + 媒体。"""
    import zipfile as _zip
    src = _zip.ZipFile(buf)
    out = io.BytesIO()
    with _zip.ZipFile(out, "w", _zip.ZIP_DEFLATED) as dst:
        for n in src.namelist():
            data = src.read(n)
            if n == "xl/worksheets/sheet1.xml":
                text = data.decode("utf-8")
                for pl in placements:
                    ref, disp = pl["ref"], pl["disp"]
                    # 公式单元格加 t="str" 与缓存值（同 WPS 原表写法）
                    m = re.search(r'<c r="%s"([^>]*)>' % re.escape(ref), text)
                    if m:
                        text = text.replace(
                            m.group(0),
                            '<c r="%s"%s t="str">' % (ref, m.group(1)), 1)
                    fstr = '_xlfn.DISPIMG("%s",1)' % disp
                    # openpyxl 写 <f>...</f><v></v> 或 <f>...</f></c>，两种情况都补上缓存值
                    text = text.replace(
                        "<f>%s</f><v></v>" % fstr,
                        '<f>%s</f><v>=DISPIMG("%s",1)</v>' % (fstr, disp),
                        1)
                    text = text.replace(
                        "<f>%s</f></c>" % fstr,
                        '<f>%s</f><v>=DISPIMG("%s",1)</v></c>' % (fstr, disp),
                        1)
                data = text.encode("utf-8")
            elif n == "xl/_rels/workbook.xml.rels":
                text = data.decode("utf-8")
                if "cellImage" not in text:
                    text = text.replace(
                        "</Relationships>",
                        '<Relationship Id="rIdCellImages" '
                        'Type="http://www.wps.cn/officeDocument/2020/cellImage" '
                        'Target="cellimages.xml"/></Relationships>')
                data = text.encode("utf-8")
            elif n == "[Content_Types].xml":
                text = data.decode("utf-8")
                if "cellimages.xml" not in text:
                    text = text.replace(
                        "</Types>",
                        '<Override PartName="/xl/cellimages.xml" '
                        'ContentType="application/vnd.wps-officedocument.cellimage+xml"/>'
                        "</Types>")
                if 'Extension="jpeg"' not in text:
                    text = text.replace(
                        "</Types>",
                        '<Default Extension="jpeg" ContentType="image/jpeg"/></Types>')
                data = text.encode("utf-8")
            dst.writestr(n, data)
        # cellimages.xml + 关系 + 媒体
        pics, rels = [], []
        for i, pl in enumerate(placements, 1):
            rels.append(
                '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org'
                '/officeDocument/2006/relationships/image" Target="media/image%d.jpeg"/>'
                % (i, i))
            pics.append(
                "<etc:cellImage><xdr:pic>"
                + '<xdr:nvPicPr><xdr:cNvPr id="%d" name="%s"/>'
                % (1000 + i, pl["disp"])
                + '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr>'
                + "</xdr:nvPicPr>"
                + '<xdr:blipFill><a:blip r:embed="rId%d"/>' % i
                + "<a:stretch><a:fillRect/></a:stretch></xdr:blipFill>"
                + '<xdr:spPr><a:xfrm><a:off x="%d" y="%d"/>'
                % (pl["x"], pl["y"])
                + '<a:ext cx="%d" cy="%d"/></a:xfrm>' % (pl["cx"], pl["cy"])
                + '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                + '<a:noFill/><a:ln w="9525"><a:noFill/></a:ln></xdr:spPr>'
                + "</xdr:pic></etc:cellImage>")
        ci = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            "<etc:cellImages "
            'xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:etc="http://www.wps.cn/officeDocument/2017/etCustomData">'
            + "".join(pics) + "</etc:cellImages>")
        dst.writestr("xl/cellimages.xml", ci.encode("utf-8"))
        rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org'
            '/package/2006/relationships">' + "".join(rels)
            + "</Relationships>")
        dst.writestr("xl/_rels/cellimages.xml.rels", rels_xml.encode("utf-8"))
        for i, pl in enumerate(placements, 1):
            dst.writestr("xl/media/image%d.jpeg" % i, pl["img"])
    out.seek(0)
    return out

def _ledger_scope_label():
    """按已选条件拼文件名片段：时间 至 时间 + 乡镇 + 小区。"""
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    town = (request.args.get("town") or "").strip()
    community = (request.args.get("community") or "").strip()
    parts = []
    if start and end:
        parts.append(f"{start}至{end}")
    elif start or end:
        parts.append(start or end)
    if town:
        parts.append(town)
    if community:
        parts.append(community)
    return " ".join(parts) if parts else "全部"


def _esc(s):
    """docx/xlsx 里写文本前的 XML 转义。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _catalog_p(text, size=24, bold=False, align="both", font=None,
               first_line=0, page_break=False):
    """目录里的一个段落。size 是半磅（24 = 12pt = 小四）。

    排版照抄原目录：正文宋体 12pt、1.5 倍行距、两端对齐、首行缩进 2 字符。
    引导线是字面文本（原目录就是手敲的「- - - - - - -」），不用制表位。
    """
    font = font or ("黑体" if bold else "宋体")
    ppr = '<w:spacing w:line="360" w:lineRule="auto"/>'
    if page_break:
        ppr += '<w:pageBreakBefore/>'
    if first_line:
        ppr += '<w:ind w:firstLine="%d" w:firstLineChars="100"/>' % first_line
    if align:
        ppr += '<w:jc w:val="%s"/>' % align
    rpr = ('<w:rPr><w:rFonts w:hint="eastAsia" w:ascii="%s" w:hAnsi="%s" '
           'w:eastAsia="%s" w:cs="%s"/>%s<w:sz w:val="%d"/>'
           '<w:szCs w:val="%d"/></w:rPr>'
           % (font, font, font, font, "<w:b/>" if bold else "", size, size))
    return ('<w:p><w:pPr>%s%s</w:pPr><w:r>%s<w:t xml:space="preserve">%s</w:t>'
            '</w:r></w:p>' % (ppr, rpr, rpr, _esc(text)))


def _fit_name(name, target=8):
    """把小区名用字间空格撑到 target 个半角单位宽（汉字算 2）。

    原目录就是这么对齐的：3 字名写成「中 央 城」凑成 4 字宽，
    这样所有行的引导线起点和页码右端才齐。超过 target 的原样返回。
    """
    name = name or ""
    w = sum(2 if ord(c) > 127 else 1 for c in name)
    if w >= target or not name:
        return name, max(w, 1)
    need = target - w
    chars = list(name)
    if len(chars) == 1:
        return chars[0] + " " * need, target
    gaps = len(chars) - 1
    out = chars[0]
    for i in range(gaps):
        out += " " * (need // gaps + (1 if i < need % gaps else 0)) + chars[i + 1]
    return out, target


def _catalog_sect(cols, space, sep=False, continuous=True):
    """目录的分节属性：A4 横向 + cols 栏（sep=True 时栏间加竖分隔线）。

    continuous=False 用下一页分节（默认），标题页靠它把正文顶到新的一页。
    """
    type_xml = '<w:type w:val="continuous"/>' if continuous else ''
    sep_xml = ' w:sep="1"' if sep else ''
    return (
        '<w:sectPr>' + type_xml
        + '<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
        + '<w:pgMar w:top="1800" w:right="1440" w:bottom="1800" w:left="1440" '
          'w:header="851" w:footer="992" w:gutter="0"/>'
        + '<w:cols w:space="%d" w:num="%d"%s/>' % (space, cols, sep_xml)
        + '<w:docGrid w:type="lines" w:linePitch="312" w:charSpace="0"/>'
        + '</w:sectPr>')


def _catalog_docx(groups):
    """生成「小区摸排表目录」docx（零依赖：手写最小 OOXML 包）。

    页码按台账排版推算：每个小区 = 1 页表格 + 照片行数页（一行照片一页）。
    """
    # 每个小区占多少页（表格页数 + 照片页数）
    plans = []
    for g in groups:
        photo_rows = sum(
            max(len(b.get("before") or []), len(b.get("after") or []))
            for b in g.get("blocks") or [])
        table_pages = max(1, len(g.get("pages") or []))
        plans.append((g, photo_rows, table_pages))

    # 每行固定总宽 33 个半角单位（1 汉字 = 2）：3 栏每栏 4369 twips ≈ 7.7cm，
    # 33 单位刚好一行放得下、不折行。中间空档用「- 」填，页码右端因此全对齐。
    ROW_W, NAME_W = 33, 8

    def width(s):
        return sum(2 if ord(c) > 127 else 1 for c in s)

    def page_field(s):
        return "%-6s页" % s

    def row(prefix, name, page_s):
        """拼一行：prefix + 小区名 + 「- 」引导线 + 页码，总宽恒为 ROW_W。"""
        tail = page_field(page_s)
        fixed = width(prefix) + width(name) + width(tail)
        n = max(1, (ROW_W - fixed) // 2)
        return prefix + name + "- " * n + " " * max(0, ROW_W - fixed - 2 * n) + tail

    # 标题单独一节、单栏居中；正文一节分 3 栏带分隔线（横向 A4 才放得下）
    body = [_catalog_p("中心城区小区摸排表目录", size=36, bold=True,
                       align="center")]
    # 标题这一节用「下一页」分节，正文（各乡镇）从新的一页开始
    body[0] = body[0].replace("</w:pPr>",
                              _catalog_sect(1, 425, continuous=False) + "</w:pPr>")
    page, seq, last_town = 1, 0, None
    for g, photo_rows, table_pages in plans:
        town = (g.get("town") or "").strip()
        if town != last_town:
            # 一个乡镇占一页：除第一个乡镇外，乡镇标题前插分页符
            body.append(_catalog_p(town or "未标乡镇", size=32, bold=True,
                                   align=None, page_break=last_town is not None))
            last_town = town
        seq += 1
        name, _w = _fit_name(g["community"], NAME_W)
        # 表格超过一页时页码写成区间（如 18-19），和「佐证照片」的写法一致
        t_rng = ("%02d" % page) if table_pages == 1 else \
                ("%02d-%02d" % (page, page + table_pages - 1))
        body.append(_catalog_p(row("%02d " % seq, name, t_rng),
                               first_line=240))
        page += table_pages
        if photo_rows:
            # 佐证照片 = 这个小区的照片页：一行照片一页
            rng = ("%02d" % page) if photo_rows == 1 else \
                  ("%02d-%02d" % (page, page + photo_rows - 1))
            body.append(_catalog_p(row("      ", "佐证照片", rng)))
            page += photo_rows

    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body>'
        + "".join(body)
        # 正文这一节：3 栏 + 栏间竖线，纸张 A4 横向、上下边距 1800 twips
        + _catalog_sect(3, 427, sep=True)
        + "</w:body></w:document>")
    ct = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types">'
        '<Default Extension="rels" ContentType="application/vnd.'
        'openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>")
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>')
    drels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"/>')
    buf = io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", drels)
    buf.seek(0)
    return buf


@app.route("/export/catalog.docx")
@require_user
def export_catalog_docx():
    """导出当前筛选范围的「小区摸排表目录」Word（页码与台账排版一致）。"""
    groups, _, _ = _ledger_groups()
    buf = _catalog_docx(groups)
    fname = f"{_ledger_scope_label()}小区摸排表目录.docx".replace("/", "-")
    log_action("导出摸排目录", f"{fname} · {len(groups)} 个小区")
    return send_file(
        buf, as_attachment=True, download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.document")


@app.route("/export/ledger.xlsx")
@require_user
def export_ledger_xlsx():
    """导出当前筛选范围的摸排台账，单个 Excel（表格内嵌原图）。"""
    groups, _, _ = _ledger_groups()
    buf = _ledger_workbook(groups)
    fname = f"{_ledger_scope_label()}小区摸排台账.xlsx".replace("/", "-")
    log_action("导出摸排台账", f"{fname} · {len(groups)} 个小区")
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# 单条记录打印：按台账格式导出仅此一条（打印用）
@app.route("/record/<int:rid>/ledger.xlsx")
@require_user
def record_ledger(rid):
    r = get_db().execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404)
    r = dict(r)
    r["_status_text"] = "已整改" if r["status"] == "closed" else "未整改"
    r["_remark"] = ""
    imgs = get_db().execute(
        "SELECT * FROM images WHERE record_id=? ORDER BY id", (rid,)
    ).fetchall()
    before = [im["filepath"] for im in imgs if im["type"] == "before"]
    after = [im["filepath"] for im in imgs if im["type"] == "after"]
    blocks = []
    if before or after:
        blocks.append({"num": 1, "before": before, "after": after})
    one = [(1, r)]
    groups = [{
        "community": r["community"] or "未填小区",
        "rows": one,
        "pages": _ledger_pages(one),
        "blocks": blocks,
    }]
    buf = _ledger_workbook(groups)
    fname = f"{(r['community'] or '未填小区')}_{r['category']}_记录{rid}.xlsx".replace("/", "-")
    log_action("打印单条记录", f"记录#{rid} {r['community'] or '未填小区'} · {r['category']}")
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------- 数据一键备份 ----------
@app.route("/backup")
@require_user
def backup():
    import zipfile as _zip
    buf = io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        db_path = DATA_DIR / "records.db"
        if db_path.exists():
            z.write(db_path, "data/records.db")
        if UPLOADS_DIR.exists():
            for f in UPLOADS_DIR.rglob("*"):
                if f.is_file():
                    z.write(f, "uploads/" + str(f.relative_to(UPLOADS_DIR)))
    buf.seek(0)
    fname = f"chengguan_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
    log_action("导出备份", fname)
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/zip")


# ---------- 辅助接口 ----------
@app.route("/api/communities")
@require_user
def api_communities():
    q = request.args.get("q", "").strip()
    return jsonify(community_autocomplete(q))


@app.route("/uploads/<path:filepath>")
@require_user
def uploads(filepath):
    # 原图是执法证据，不能无鉴权访问
    return send_from_directory(UPLOADS_DIR, filepath)


@app.route("/manifest.json")
def manifest():
    return send_from_directory(BASE_DIR, "manifest.json",
                               mimetype="application/manifest+json")


@app.route("/sw.js")
def sw():
    return send_from_directory(BASE_DIR, "sw.js",
                               mimetype="application/javascript")


if __name__ == "__main__":
    # 生产走 Dockerfile 里的 gunicorn；这里只是本地调试入口。
    # 不默认 debug（Werkzeug 调试器可 RCE），需要时显式 FLASK_DEBUG=1。
    app.run(host="127.0.0.1", port=5000,
            debug=os.environ.get("FLASK_DEBUG") == "1")
