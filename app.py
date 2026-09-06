# -*- coding: utf-8 -*-
"""社区城管日常巡查记录平台 — Flask 主程序。

移动端优先 · 单账号登录 · 按两个乡镇（饶州街道 / 鄱阳镇）归类 · SQLite 单文件 · 原图保留。
"""
import io
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta
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
PROGRESS_LABELS = {"investigating": "调查中", "filed": "已立案", "closed": "已办结"}


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
        for col in ("deadline", "lead_dept", "assist_dept"):
            if col not in cols:
                db.execute(
                    "ALTER TABLE records ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % col)
                print("[migrate] records 增加 %s 列" % col)
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
    month = (request.args.get("month") or "").strip()
    if month:
        where = (where + " AND " if where else "") + col + " LIKE ?"
        params.append(month + "%")
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    if start:
        where = (where + " AND " if where else "") + col + " >= ?"
        params.append(start + " 00:00")
    if end:
        where = (where + " AND " if where else "") + col + " <= ?"
        params.append(end + " 23:59")
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
                d = datetime.strptime(r["created_at"][:10], "%Y-%m-%d").date()
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
    sql += " ORDER BY id DESC"
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
        if town not in TOWNS:
            return render_template("create.html", error="请选择所属乡镇",
                                   categories=CATEGORIES), 400
        # 小区、分类、描述都可以留空（分类缺省记"其他"），之后可在详情页编辑补全
        db = get_db()
        cur = db.execute(
            "INSERT INTO records(town, community, category, description, "
            "status, deadline, lead_dept, assist_dept, reporter, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (town, community, category, description, "pending", deadline,
             lead_dept, assist_dept, current_user()["name"], now()),
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
        "create.html", error=None, categories=CATEGORIES,
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
                           progress_labels=PROGRESS_LABELS,
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
        if town not in TOWNS:
            return render_template("edit.html", r=r, categories=CATEGORIES,
                                   error="请选择所属乡镇"), 400
        get_db().execute(
            "UPDATE records SET town=?, community=?, category=?, description=?, "
            "deadline=?, lead_dept=?, assist_dept=? WHERE id=?",
            (town, community, category, description, deadline, lead_dept,
             assist_dept, rid),
        )
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
        db.execute(
            "UPDATE records SET status='closed', result=?, closed_at=?, "
            "deadline=? WHERE id=?", (result, now(), deadline, rid),
        )
        db.commit()
        log_action("整改销号", f"记录#{rid} {r['community'] or '未填小区'} · {r['category']}")
        return redirect(url_for("detail", rid=rid))
    return render_template("close.html", r=r, error=None)


# ---------- 案件 ----------
@app.route("/cases", methods=["GET", "POST"])
@require_user
def cases():
    if request.method == "POST":
        town = (request.form.get("town") or "").strip()
        case_no = (request.form.get("case_no") or "").strip()
        case_name = (request.form.get("case_name") or "").strip()
        progress = request.form.get("progress", "investigating")
        fine = request.form.get("fine_amount", "0").strip() or "0"
        if town not in TOWNS or not case_name:
            return render_template(
                "cases.html", error="乡镇和案件名称要填", rows=[],
                progress_labels=PROGRESS_LABELS,
            ), 400
        get_db().execute(
            "INSERT INTO cases(town, case_no, case_name, progress, "
            "fine_amount, reporter, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (town, case_no, case_name, progress, float(fine),
             current_user()["name"], now(), now()),
        )
        get_db().commit()
        log_action("登记案件", f"{town} · {case_name}")
        return redirect(url_for("cases"))

    town_q = (request.args.get("town") or request.args.get("team") or "").strip()
    progress_q = request.args.get("progress", "")
    month = request.args.get("month", "")

    where, params = "", []
    if town_q in TOWNS:
        where, params = "town = ?", [town_q]
    if progress_q in PROGRESS_LABELS:
        where = (where + " AND " if where else "") + "progress = ?"
        params.append(progress_q)
    if month:
        where = (where + " AND " if where else "") + "created_at LIKE ?"
        params.append(month + "%")
    # 统计页带起止日期下钻过来时也要认，否则这里会静默退回按月的口径
    for key in ("start", "end"):
        val = (request.args.get(key) or "").strip()
        if not val:
            continue
        op = ">=" if key == "start" else "<="
        where = (where + " AND " if where else "") + f"created_at {op} ?"
        params.append(val + (" 00:00" if key == "start" else " 23:59"))
    sql = "SELECT * FROM cases" + (" WHERE " + where if where else "")
    sql += " ORDER BY id DESC"
    rows = get_db().execute(sql, params).fetchall()
    return render_template("cases.html", error=None, rows=rows,
                           progress_labels=PROGRESS_LABELS,
                           sel={"town": town_q, "progress": progress_q,
                                "month": month,
                                "start": (request.args.get("start") or "").strip(),
                                "end": (request.args.get("end") or "").strip()})


@app.route("/case/<int:cid>/update", methods=["POST"])
@require_user
def case_update(cid):
    c = get_db().execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
    progress = request.form.get("progress", "investigating")
    fine = (request.form.get("fine_amount") or "").strip() or "0"
    db = get_db()
    db.execute(
        "UPDATE cases SET progress=?, fine_amount=?, updated_at=? WHERE id=?",
        (progress, float(fine), now(), cid),
    )
    db.commit()
    log_action("更新案件", f"案件#{cid} 进度={progress} 罚款={fine}")
    return redirect(url_for("cases"))


@app.route("/case/<int:cid>/edit", methods=["GET", "POST"])
@require_user
def case_edit(cid):
    c = get_db().execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
    if not c:
        abort(404)
    if request.method == "POST":
        town = (request.form.get("town") or "").strip()
        case_no = (request.form.get("case_no") or "").strip()
        case_name = (request.form.get("case_name") or "").strip()
        fine = (request.form.get("fine_amount") or "").strip() or "0"
        if town not in TOWNS or not case_name:
            return render_template("case_edit.html", c=c,
                                   error="乡镇和案件名称要填"), 400
        db = get_db()
        db.execute(
            "UPDATE cases SET town=?, case_no=?, case_name=?, fine_amount=?, "
            "updated_at=? WHERE id=?",
            (town, case_no, case_name, float(fine), now(), cid),
        )
        db.commit()
        log_action("编辑案件", f"案件#{cid} {case_name}")
        return redirect(url_for("cases"))
    return render_template("case_edit.html", c=c, error=None)


@app.route("/case/<int:cid>/delete", methods=["POST"])
@require_user
def case_delete(cid):
    c = get_db().execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
    if not c:
        abort(404)
    get_db().execute("DELETE FROM cases WHERE id=?", (cid,))
    get_db().commit()
    log_action("删除案件", f"案件#{cid} {c['case_name']}")
    flash("案件已删除", "ok")
    return redirect(url_for("cases"))


# ---------- 统计 ----------
# 时间口径：create 按录入时间；smart 智能（已结案按结案时间、未结案按录入时间）；
# close 按结案时间。默认 smart —— 一条记录算在它该算的时段里。
BASIS_LABELS = {
    "create": "按录入时间",
    "smart": "智能口径",
    "close": "按结案时间",
}
BASIS_HINTS = {
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
    if start:
        cond.append(col + " >= ?")
        params.append(start + " 00:00")
    if end:
        cond.append(col + " <= ?")
        params.append(end + " 23:59")
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

    # 案件没有结案时间，始终按登记时间
    ccond, cparams = [], []
    if town in TOWNS:
        ccond.append("town = ?")
        cparams.append(town)
    if start:
        ccond.append("created_at >= ?")
        cparams.append(start + " 00:00")
    if end:
        ccond.append("created_at <= ?")
        cparams.append(end + " 23:59")
    cscope = " AND ".join(ccond)

    case_total = db.execute(
        "SELECT COUNT(*) c FROM cases WHERE " + cscope, cparams).fetchone()["c"]
    case_fine = db.execute(
        "SELECT COALESCE(SUM(fine_amount),0) s FROM cases WHERE " + cscope, cparams,
    ).fetchone()["s"]
    case_by_progress = db.execute(
        "SELECT progress, COUNT(*) c FROM cases WHERE " + cscope +
        " GROUP BY progress", cparams).fetchall()
    case_by_town = db.execute(
        "SELECT town, COUNT(*) c FROM cases WHERE " + cscope +
        " GROUP BY town", cparams).fetchall()

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
        case_total=case_total, case_fine=case_fine,
        case_by_progress=case_by_progress, case_by_town=case_by_town,
        progress_labels=PROGRESS_LABELS,
        sel={"town": town if town in TOWNS else ""},
        q={"start": start, "end": end, "basis": basis},
    )



# ---------- 导出筛选参数（通用） ----------
def _export_filters():
    """台账导出的筛选参数，返回 (where, params, start, end, month)。

    口径钉死在录入时间：URL 里手填 basis 不能让下载和页面上的预览不一致。
    start/end 已含在 where 里，单独返回是为了给模板回填日期框、拼下载链接。
    """
    where, params = _record_where("create")
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
    groups = []
    for comm in sorted(by_comm):
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
            "pad": max(0, 9 - len(rows)),  # 预览/表格固定 9 行序号空间
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
    where, params = _record_where("create")
    sql = "SELECT DISTINCT community FROM records WHERE community != ''"
    if where:
        sql += " AND " + where
    cnames = {r["community"] for r in get_db().execute(sql, params).fetchall()}
    return render_template(
        "ledger.html", groups=groups, deps=deps,
        start=start, end=end,
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
        category=request.args.get("category", "")))


def _ledger_workbook(groups):
    """生成摸排台账工作簿：单表连续排版，页脚与原表一致；照片用 WPS 嵌入单元格（DISPIMG）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter
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
    # 纸张：A4 横向 + 原表页边距
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = 9
    ws.page_margins.left = ws.page_margins.right = 0.590277777777778
    ws.page_margins.top = ws.page_margins.bottom = 0.751388888888889
    ws.page_margins.header = ws.page_margins.footer = 0.298611111111111
    # 页脚与原表一致：居中「第 &P 页」
    ws.oddFooter.center.text = "第 &P 页"
    ws.oddFooter.center.size = 9
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.column_dimensions["J"].width = 9.64  # 原表右侧余量列
    if not groups:  # 无数据时也要有可见内容
        ws["A1"] = "当前范围内没有巡查记录"
        ws["A1"].font = title_font
    # 单表连续排版：下一个小区的表格接在上一个小区照片下面，不强制分页
    placements = []  # 嵌入单元格图片 {ref, disp, x, y, cx, cy, png}
    y_pt = 0.0       # 当前行顶距表顶的点数（算图片 y 偏移）
    r = 1
    for g in groups:
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
        # 数据行：固定 9 行序号空间，不足补空行；超过 9 行顺延
        data_start = r
        for num, rec in g["rows"]:
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
        while r < data_start + 9:  # 补足固定 9 行空间（带边框空行）
            for col in range(1, 10):
                ws.cell(row=r, column=col).border = border
            ws.row_dimensions[r].height = 45
            r += 1
            y_pt += 45
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
    groups = [{
        "community": r["community"] or "未填小区",
        "rows": [(1, r)],
        "pad": 8,
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
