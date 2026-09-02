# -*- coding: utf-8 -*-
"""社区城管日常巡查记录平台 — Flask 主程序。

移动端优先 · 账号角色权限（主管理员/办公室/中队长/队员） · SQLite 单文件 · 原图保留。
"""
import io
import os
import re
import secrets
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

ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD", "").strip()

# ---------- 常量配置（增改一处全局生效） ----------
TEAMS = [
    "鄱阳镇社区一中队", "鄱阳镇社区二中队",
    "饶州街道社区一中队", "饶州街道社区二中队",
]
UNITS = TEAMS + ["办公室", "大队"]  # 大队 = 主管理员所在单位
CATEGORIES = [
    "违法搭建", "牛皮癣小广告", "乱堆放杂物", "电动车乱停放",
    "流动摊贩", "出店经营", "毁坏绿化", "占道经营",
    "破坏市政设施", "乱倒垃圾", "噪音扰民", "其他",
]
PROGRESS_LABELS = {"investigating": "调查中", "filed": "已立案", "closed": "已办结"}
ROLE_LABELS = {"super": "主管理员", "office": "办公室管理员",
               "captain": "中队长", "vice-captain": "副中队长", "member": "队员"}
# 各角色可创建的账号角色
ADD_ROLES = {
    "super": [("captain", "中队长"), ("vice-captain", "副中队长"),
              ("member", "队员"), ("office", "办公室管理员")],
    "office": [("vice-captain", "副中队长"), ("member", "队员")],
    "captain": [("vice-captain", "副中队长"), ("member", "队员")],
}


def random_pin() -> str:
    return f"{secrets.randbelow(10000):04d}"


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


def base_url():
    """对外分享链接用的域名：主管理员在账号管理页配置，未配置则用当前访问域名。"""
    u = (get_setting("base_url") or "").strip().rstrip("/")
    if not u:
        u = request.host_url.rstrip("/")
    return u


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
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "chengguan-local-dev-secret")
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
            unit TEXT NOT NULL,
            role TEXT NOT NULL,          -- super / office / captain / member
            pin TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT NOT NULL,
            community TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result TEXT DEFAULT '',
            deadline TEXT NOT NULL DEFAULT '',
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
            team TEXT NOT NULL,
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
        # 旧库迁移：records 缺 deadline 列时补上（整改期限）
        cols = [r[1] for r in db.execute("PRAGMA table_info(records)").fetchall()]
        if "deadline" not in cols:
            db.execute("ALTER TABLE records ADD COLUMN deadline TEXT NOT NULL DEFAULT ''")
            print("[migrate] records 增加 deadline 列")
        cur = db.execute("SELECT COUNT(*) c FROM users")
        if cur.fetchone()["c"] == 0:
            db.execute(
                "INSERT INTO users(name, unit, role, pin, created_at) "
                "VALUES(?,?,?,?,?)",
                ("主管理员", "大队", "super", "0000", now()),
            )
            print("[init] 已创建主管理员账号：单位=大队，姓名=主管理员，初始密码=0000")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


init_db()


# ---------- 访问控制 ----------
@app.before_request
def gate_keeper():
    if ACCESS_PASSWORD and not session.get("gate_ok"):
        if request.endpoint not in ("gate", "static"):
            return redirect(url_for("gate"))


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def require_user(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("personnel"))
        return view(*args, **kwargs)
    return wrapped


def scope_where(team_col="team", reporter_col="reporter"):
    """按角色返回数据可见范围 (where子句, 参数列表)。

    主管理员/办公室：全部；中队长/副中队长：本中队全部；队员：仅自己上报的。
    """
    u = current_user()
    if u["role"] in ("super", "office"):
        return "", []
    if u["role"] in ("captain", "vice-captain"):
        return f"{team_col} = ?", [u["unit"]]
    return f"{reporter_col} = ?", [u["name"]]


def can_view_record(r):
    u = current_user()
    if u["role"] in ("super", "office"):
        return True
    if u["role"] in ("captain", "vice-captain"):
        return r["team"] == u["unit"]
    return r["reporter"] == u["name"]


def can_view_case(c):
    u = current_user()
    if u["role"] in ("super", "office"):
        return True
    if u["role"] in ("captain", "vice-captain"):
        return c["team"] == u["unit"]
    return c["reporter"] == u["name"]


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


# ---------- 站点密码门 ----------
@app.route("/gate", methods=["GET", "POST"])
def gate():
    if request.method == "POST":
        if request.form.get("password") == ACCESS_PASSWORD:
            session["gate_ok"] = True
            return redirect(url_for("index"))
        return render_template("gate.html", error="密码不对")
    return render_template("gate.html", error=None)


# ---------- 登录 ----------
@app.route("/personnel", methods=["GET", "POST"])
def personnel():
    if request.method == "POST":
        unit = (request.form.get("unit") or "").strip()
        name = (request.form.get("name") or "").strip()
        pin = (request.form.get("pin") or "").strip()
        u = get_db().execute(
            "SELECT * FROM users WHERE unit=? AND name=? AND pin=?",
            (unit, name, pin),
        ).fetchone()
        if u:
            session.permanent = True
            session["uid"] = u["id"]
            return redirect(url_for("index"))
        return render_template("personnel.html", error="单位、姓名或密码不对",
                               units=UNITS)
    return render_template("personnel.html", error=None, units=UNITS)


@app.route("/api/users")
def api_users():
    unit = request.args.get("unit", "").strip()
    if unit not in UNITS:
        return jsonify([])
    rows = get_db().execute(
        "SELECT name FROM users WHERE unit=? ORDER BY id", (unit,)
    ).fetchall()
    return jsonify([r["name"] for r in rows])


@app.route("/logout")
def logout():
    session.pop("uid", None)
    return redirect(url_for("personnel"))


# ---------- 账号管理（主管理员/办公室/中队长） ----------
def admin_access():
    """账号管理页面与操作权限校验，返回当前用户或 abort。"""
    u = current_user()
    if not u:
        return redirect(url_for("personnel"))
    if u["role"] not in ("super", "office", "captain"):
        abort(403)
    return u


@app.route("/admin")
@require_user
def admin():
    u = admin_access()
    db = get_db()
    if u["role"] == "captain":
        users = db.execute(
            "SELECT * FROM users WHERE unit=? ORDER BY id", (u["unit"],)
        ).fetchall()
        add_units = [u["unit"]]
    elif u["role"] == "office":
        users = db.execute(
            "SELECT * FROM users ORDER BY unit, id"
        ).fetchall()
        add_units = TEAMS
    else:  # super
        users = db.execute(
            "SELECT * FROM users ORDER BY unit, id"
        ).fetchall()
        add_units = TEAMS + ["办公室"]

    # 密码可见范围：主管理员看全部；办公室看不到主管理员的；中队长只看本队队员(副中队长/队员)
    share_base = base_url()
    items = []
    for row in users:
        row = dict(row)
        if u["role"] == "super":
            row["show_pin"] = True
        elif u["role"] == "office":
            row["show_pin"] = row["role"] != "super"
        else:  # captain
            row["show_pin"] = row["role"] in ("vice-captain", "member")
        row["can_reset"] = u["role"] == "super"  # 重置密码仅主管理员
        if row["show_pin"]:
            row["share_url"] = (
                share_base + url_for("personnel",
                                     unit=row["unit"], name=row["name"],
                                     pin=row["pin"])
            )
        items.append(row)

    logs = []
    if u["role"] == "super":
        logs = get_db().execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT 100"
        ).fetchall()

    return render_template("admin.html", users=items, teams=TEAMS,
                           role_labels=ROLE_LABELS, user=u,
                           add_units=add_units,
                           base_url=get_setting("base_url", ""),
                           logs=logs,
                           team_roles=[r for r in ADD_ROLES[u["role"]] if r[0] != "office"],
                           office_roles=[r for r in ADD_ROLES[u["role"]] if r[0] == "office"])


@app.route("/admin/settings", methods=["POST"])
@require_user
def admin_settings():
    u = admin_access()
    if u["role"] != "super":
        flash("只有主管理员可以配置域名", "error")
        return redirect(url_for("admin"))
    val = (request.form.get("base_url") or "").strip().rstrip("/")
    if val and not val.startswith(("http://", "https://")):
        flash("域名要以 http:// 或 https:// 开头，例如 http://192.168.50.65:8755", "error")
        return redirect(url_for("admin"))
    set_setting("base_url", val)
    flash("分享域名已保存，分享链接将使用：" + (val or "当前访问地址"), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/add", methods=["POST"])
@require_user
def admin_add():
    u = admin_access()
    name = (request.form.get("name") or "").strip()
    unit = request.form.get("unit", "")
    role = request.form.get("role", "")
    if not name:
        flash("姓名不能为空", "error")
        return redirect(url_for("admin"))
    if u["role"] == "captain":
        if unit and unit != u["unit"]:
            flash("只能给自己中队添加人员", "error")
            return redirect(url_for("admin"))
        unit = u["unit"]
    if u["role"] in ("office", "captain") and role not in ("vice-captain", "member"):
        flash("只能添加副中队长和队员", "error")
        return redirect(url_for("admin"))
    if u["role"] == "super":
        valid = (unit in TEAMS and role in ("captain", "vice-captain", "member")) or \
                (unit == "办公室" and role == "office")
    elif u["role"] == "office":
        valid = unit in TEAMS and role in ("vice-captain", "member")
    else:
        valid = unit == u["unit"] and role in ("vice-captain", "member")
    if not valid:
        flash("单位与角色组合不合法", "error")
        return redirect(url_for("admin"))
    if get_db().execute(
            "SELECT 1 FROM users WHERE unit=? AND name=?", (unit, name)
    ).fetchone():
        flash(f"{unit} 已存在同名人员", "error")
        return redirect(url_for("admin"))
    pin = random_pin()
    get_db().execute(
        "INSERT INTO users(name, unit, role, pin, created_at) VALUES(?,?,?,?,?)",
        (name, unit, role, pin, now()),
    )
    get_db().commit()
    log_action("创建账号", f"{unit} · {name}（{ROLE_LABELS[role]}）")
    flash(f"已创建 {unit}·{name}（{ROLE_LABELS[role]}），初始密码 {pin}，请转交本人", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/reset/<int:uid>", methods=["POST"])
@require_user
def admin_reset(uid):
    u = admin_access()
    if u["role"] != "super":
        flash("只有主管理员可以重置密码", "error")
        return redirect(url_for("admin"))
    target = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not target:
        abort(404)
    pin = random_pin()
    get_db().execute("UPDATE users SET pin=? WHERE id=?", (pin, uid))
    get_db().commit()
    log_action("重置密码", f"{target['name']}（{target['unit']}）")
    flash(f"已重置 {target['name']} 密码，新密码 {pin}，请转交本人", "ok")
    return redirect(url_for("admin"))


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
@app.route("/")
@require_user
def index():
    u = current_user()
    team = request.args.get("team", "")
    category = request.args.get("category", "")
    reporter = request.args.get("reporter", "")
    status = request.args.get("status", "")
    month = request.args.get("month", "")
    community = request.args.get("community", "")
    q = (request.args.get("q") or "").strip()

    where, params = scope_where()
    if where and team:
        where += " AND team = ?"
        params.append(team)
    elif where:
        pass  # 中队长/队员的 team 已由 scope 限定
    else:
        if team:
            where = "team = ?"
            params = [team]
    if category:
        where = (where + " AND " if where else "") + "category = ?"
        params.append(category)
    if reporter:
        where = (where + " AND " if where else "") + "reporter = ?"
        params.append(reporter)
    if status in ("pending", "closed"):
        where = (where + " AND " if where else "") + "status = ?"
        params.append(status)
    if month:
        where = (where + " AND " if where else "") + "created_at LIKE ?"
        params.append(month + "%")
    if community:
        where = (where + " AND " if where else "") + "community = ?"
        params.append(community)
    if q:
        where = (where + " AND " if where else "") + "(community LIKE ? OR description LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]

    sql = "SELECT * FROM records"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY id DESC"
    records = [dict(r) for r in get_db().execute(sql, params).fetchall()]
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

    # 统计也按权限范围
    this_month = datetime.now().strftime("%Y-%m")
    stat_where = where
    stat_params = list(params)
    def count_where(extra=""):
        w = stat_where
        if extra:
            w = (w + " AND " if w else "") + extra
        sql2 = "SELECT COUNT(*) c FROM records"
        if w:
            sql2 += " WHERE " + w
        return get_db().execute(sql2, stat_params).fetchone()["c"]

    stats = {
        "month_new": count_where("created_at LIKE ?") if False else
            get_db().execute(
                "SELECT COUNT(*) c FROM records WHERE " +
                ((stat_where + " AND ") if stat_where else "") +
                "created_at LIKE ?", stat_params + [this_month + "%"],
            ).fetchone()["c"],
        "pending": get_db().execute(
            "SELECT COUNT(*) c FROM records WHERE " +
            ((stat_where + " AND ") if stat_where else "") + "status='pending'",
            stat_params,
        ).fetchone()["c"],
        "closed": get_db().execute(
            "SELECT COUNT(*) c FROM records WHERE " +
            ((stat_where + " AND ") if stat_where else "") + "status='closed'",
            stat_params,
        ).fetchone()["c"],
    }

    # 筛选下拉选项
    can_all = u["role"] in ("super", "office")
    team_options = TEAMS if can_all else []
    if u["role"] in ("captain", "vice-captain"):
        reporter_rows = get_db().execute(
            "SELECT name FROM users WHERE unit=? ORDER BY id", (u["unit"],)
        ).fetchall()
    elif can_all:
        reporter_rows = get_db().execute(
            "SELECT name FROM users ORDER BY unit, id"
        ).fetchall()
    else:
        reporter_rows = []

    return render_template(
        "index.html", records=records, stats=stats, user=u,
        categories=CATEGORIES, role_labels=ROLE_LABELS,
        team_options=team_options, reporter_options=[r["name"] for r in reporter_rows],
        can_all=can_all,
        sel={"team": team, "category": category, "reporter": reporter,
             "status": status, "month": month, "community": community, "q": q},
        this_month=this_month,
    )


@app.route("/create", methods=["GET", "POST"])
@require_user
def create():
    u = current_user()
    if request.method == "POST":
        if u["role"] in ("captain", "vice-captain", "member"):
            team = u["unit"]
        else:
            team = (request.form.get("team") or "").strip()
        community = (request.form.get("community") or "").strip()
        category = (request.form.get("category") or "").strip() or "其他"
        description = (request.form.get("description") or "").strip()
        deadline = (request.form.get("deadline") or "").strip()
        if team not in TEAMS:
            return render_template("create.html", error="请选择所属中队",
                                   teams=TEAMS, categories=CATEGORIES,
                                   user=u), 400
        # 小区、分类、描述都可以留空（分类缺省记“其他”），传了之后可在详情页编辑补全
        db = get_db()
        cur = db.execute(
            "INSERT INTO records(team, community, category, description, "
            "status, deadline, reporter, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (team, community, category, description, "pending", deadline,
             u["name"], now()),
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
                   f"{team} · {community or '未填小区'} · {category}")
        bump_community(community)
        return redirect(url_for("detail", rid=rid))
    return render_template("create.html", error=None,
                           teams=TEAMS, categories=CATEGORIES, user=u, old=None)


@app.route("/record/<int:rid>")
@require_user
def detail(rid):
    r = get_db().execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404)
    if not can_view_record(r):
        abort(403)
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
    u = current_user()
    r = get_db().execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404)
    if not can_view_record(r):
        abort(403)
    if request.method == "POST":
        team = (request.form.get("team") or "").strip()
        community = (request.form.get("community") or "").strip()
        category = (request.form.get("category") or "").strip() or "其他"
        description = (request.form.get("description") or "").strip()
        deadline = (request.form.get("deadline") or "").strip()
        if team not in TEAMS:
            return render_template("edit.html", r=r, teams=TEAMS,
                                   categories=CATEGORIES, user=u,
                                   error="请选择所属中队"), 400
        get_db().execute(
            "UPDATE records SET team=?, community=?, category=?, description=?, "
            "deadline=? WHERE id=?",
            (team, community, category, description, deadline, rid),
        )
        get_db().commit()
        log_action("编辑巡查记录", f"记录#{rid} {community or '未填小区'} · {category}")
        bump_community(community)
        return redirect(url_for("detail", rid=rid))
    return render_template("edit.html", r=r, teams=TEAMS, categories=CATEGORIES,
                           user=u, error=None)


@app.route("/record/<int:rid>/delete", methods=["POST"])
@require_user
def delete_record(rid):
    r = get_db().execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404)
    if not can_view_record(r):
        abort(403)
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
    flash("记录已删除", "ok")
    return redirect(url_for("index"))


@app.route("/record/<int:rid>/close", methods=["GET", "POST"])
@require_user
def close(rid):
    r = get_db().execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404)
    if not can_view_record(r):
        abort(403)
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


# ---------- 案件（并入巡查主流程） ----------
@app.route("/cases", methods=["GET", "POST"])
@require_user
def cases():
    u = current_user()
    db = get_db()
    if request.method == "POST":
        if u["role"] in ("captain", "vice-captain", "member"):
            team = u["unit"]
        else:
            team = (request.form.get("team") or "").strip()
        case_name = (request.form.get("case_name") or "").strip()
        progress = request.form.get("progress", "investigating")
        fine = request.form.get("fine_amount", "0").strip() or "0"
        if team not in TEAMS or not case_name:
            return render_template(
                "cases.html", error="中队和案件名称要填", rows=[],
                teams=TEAMS, progress_labels=PROGRESS_LABELS, user=u,
            ), 400
        db.execute(
            "INSERT INTO cases(team, case_name, progress, fine_amount, "
            "reporter, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (team, case_name, progress, float(fine), u["name"],
             now(), now()),
        )
        db.commit()
        log_action("登记案件", f"{team} · {case_name}")
        return redirect(url_for("cases"))

    month = request.args.get("month", "")
    team_q = request.args.get("team", "")
    progress_q = request.args.get("progress", "")

    where, params = scope_where()
    if team_q:
        where = (where + " AND " if where else "") + "team = ?"
        params.append(team_q)
    if progress_q in PROGRESS_LABELS:
        where = (where + " AND " if where else "") + "progress = ?"
        params.append(progress_q)
    if month:
        where = (where + " AND " if where else "") + "created_at LIKE ?"
        params.append(month + "%")
    sql = "SELECT * FROM cases"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY id DESC"
    rows = db.execute(sql, params).fetchall()
    return render_template("cases.html", error=None, rows=rows,
                           teams=TEAMS, progress_labels=PROGRESS_LABELS,
                           user=u, can_all=u["role"] in ("super", "office"),
                           sel={"team": team_q, "progress": progress_q,
                                "month": month})


@app.route("/case/<int:cid>/update", methods=["POST"])
@require_user
def case_update(cid):
    c = get_db().execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
    if not c or not can_view_case(c):
        abort(404)
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
    u = current_user()
    c = get_db().execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
    if not c or not can_view_case(c):
        abort(404)
    if request.method == "POST":
        if u["role"] in ("super", "office"):
            team = (request.form.get("team") or "").strip()
        else:
            team = c["team"]
        case_name = (request.form.get("case_name") or "").strip()
        fine = (request.form.get("fine_amount") or "").strip() or "0"
        if team not in TEAMS or not case_name:
            return render_template("case_edit.html", c=c, teams=TEAMS,
                                   user=u, error="中队和案件名称要填"), 400
        db = get_db()
        db.execute(
            "UPDATE cases SET team=?, case_name=?, fine_amount=?, updated_at=? "
            "WHERE id=?", (team, case_name, float(fine), now(), cid),
        )
        db.commit()
        log_action("编辑案件", f"案件#{cid} {case_name}")
        return redirect(url_for("cases"))
    return render_template("case_edit.html", c=c, teams=TEAMS, user=u, error=None)


@app.route("/case/<int:cid>/delete", methods=["POST"])
@require_user
def case_delete(cid):
    c = get_db().execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
    if not c or not can_view_case(c):
        abort(404)
    get_db().execute("DELETE FROM cases WHERE id=?", (cid,))
    get_db().commit()
    log_action("删除案件", f"案件#{cid} {c['case_name']}")
    flash("案件已删除", "ok")
    return redirect(url_for("cases"))


# ---------- 居民投诉 ----------
@app.route("/complaints", methods=["GET", "POST"])
@require_user
def complaints():
    u = current_user()
    db = get_db()
    if request.method == "POST":
        if u["role"] in ("captain", "vice-captain", "member"):
            team = u["unit"]
        else:
            team = (request.form.get("team") or "").strip()
        community = (request.form.get("community") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        content = (request.form.get("content") or "").strip()
        if team not in TEAMS or not community or not content:
            return render_template(
                "complaints.html", error="小区和投诉内容要填", rows=[],
                teams=TEAMS, user=u, can_all=u["role"] in ("super", "office"),
            ), 400
        cur = db.execute(
            "INSERT INTO complaints(team, community, phone, content, status, "
            "reporter, created_at) VALUES(?,?,?,?,?,?,?)",
            (team, community, phone, content, "pending", u["name"], now()),
        )
        cid = cur.lastrowid
        saved = save_photos(request.files.getlist("photos"),
                            f"complaints/{cid}")
        db.executemany(
            "INSERT INTO images(record_id, type, filepath, created_at) "
            "VALUES(?,?,?,?)",
            [(cid, "complaint", p[0], now()) for p in saved],
        )
        db.commit()
        log_action("登记投诉", f"{team} · {community or '未填小区'}")
        bump_community(community)
        return redirect(url_for("complaints"))
    where, params = scope_where()
    sql = "SELECT * FROM complaints"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY id DESC"
    rows = db.execute(sql, params).fetchall()
    for r in rows:
        r = dict(r)
        img = db.execute(
            "SELECT filepath FROM images WHERE record_id=? AND type='complaint' "
            "ORDER BY id LIMIT 1", (r["id"],)
        ).fetchone()
        r["thumb"] = thumb_of(img["filepath"]) if img else None
    return render_template("complaints.html", error=None, rows=rows,
                           teams=TEAMS, user=u,
                           can_all=u["role"] in ("super", "office"))


@app.route("/complaint/<int:cid>/toggle", methods=["POST"])
@require_user
def complaint_toggle(cid):
    c = get_db().execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone()
    if not c:
        abort(404)
    u = current_user()
    if u["role"] in ("super", "office"):
        pass
    elif u["role"] in ("captain", "vice-captain"):
        if c["team"] != u["unit"]:
            abort(403)
    else:
        if c["reporter"] != u["name"]:
            abort(403)
    new_status = "done" if c["status"] == "pending" else "pending"
    db = get_db()
    db.execute(
        "UPDATE complaints SET status=?, handler=?, handled_at=? WHERE id=?",
        (new_status, u["name"] if new_status == "done" else "", now(), cid),
    )
    db.commit()
    log_action("处理投诉", f"投诉#{cid} → {'已处理' if new_status == 'done' else '待处理'}")
    return redirect(url_for("complaints"))


def complaint_access(c):
    u = current_user()
    if u["role"] in ("super", "office"):
        return True
    if u["role"] in ("captain", "vice-captain"):
        return c["team"] == u["unit"]
    return c["reporter"] == u["name"]


@app.route("/complaint/<int:cid>/edit", methods=["GET", "POST"])
@require_user
def complaint_edit(cid):
    u = current_user()
    c = get_db().execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone()
    if not c or not complaint_access(c):
        abort(404)
    if request.method == "POST":
        community = (request.form.get("community") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        content = (request.form.get("content") or "").strip()
        get_db().execute(
            "UPDATE complaints SET community=?, phone=?, content=? WHERE id=?",
            (community, phone, content, cid),
        )
        get_db().commit()
        log_action("编辑投诉", f"投诉#{cid} {community or '未填小区'}")
        return redirect(url_for("complaints"))
    return render_template("complaint_edit.html", c=c, user=u, error=None)


@app.route("/complaint/<int:cid>/delete", methods=["POST"])
@require_user
def complaint_delete(cid):
    c = get_db().execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone()
    if not c or not complaint_access(c):
        abort(404)
    images = get_db().execute(
        "SELECT filepath FROM images WHERE record_id=? AND type='complaint'", (cid,)
    ).fetchall()
    db = get_db()
    db.execute("DELETE FROM images WHERE record_id=? AND type='complaint'", (cid,))
    db.execute("DELETE FROM complaints WHERE id=?", (cid,))
    db.commit()
    for img in images:
        for suffix in (img["filepath"], thumb_of(img["filepath"])):
            p = UPLOADS_DIR / suffix
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
    log_action("删除投诉", f"投诉#{cid} {c['community'] or '未填小区'}")
    flash("投诉已删除", "ok")
    return redirect(url_for("complaints"))


# ---------- 统计 ----------
@app.route("/stats")
@require_user
def stats():
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    prefix = month + "%"
    where, params = scope_where()
    rw = (where + " AND ") if where else ""
    db = get_db()

    total = db.execute(
        f"SELECT COUNT(*) c FROM records WHERE {rw}created_at LIKE ?",
        params + [prefix],
    ).fetchone()["c"]
    closed = db.execute(
        f"SELECT COUNT(*) c FROM records WHERE {rw}created_at LIKE ? "
        "AND status='closed'", params + [prefix],
    ).fetchone()["c"]

    by_team = db.execute(
        f"SELECT team, COUNT(*) c, SUM(status='closed') closed FROM records "
        f"WHERE {rw}created_at LIKE ? GROUP BY team", params + [prefix],
    ).fetchall()
    by_category = db.execute(
        f"SELECT category, COUNT(*) c FROM records WHERE {rw}created_at LIKE ? "
        "GROUP BY category ORDER BY c DESC", params + [prefix],
    ).fetchall()
    by_person = db.execute(
        f"SELECT reporter, COUNT(*) c, SUM(status='closed') closed FROM records "
        f"WHERE {rw}created_at LIKE ? GROUP BY reporter ORDER BY c DESC",
        params + [prefix],
    ).fetchall()
    by_community = db.execute(
        f"SELECT community, COUNT(*) c FROM records WHERE {rw}created_at LIKE ? "
        "GROUP BY community ORDER BY c DESC LIMIT 10", params + [prefix],
    ).fetchall()

    case_total = db.execute(
        f"SELECT COUNT(*) c FROM cases WHERE {rw}created_at LIKE ?",
        params + [prefix],
    ).fetchone()["c"]
    case_fine = db.execute(
        f"SELECT COALESCE(SUM(fine_amount),0) s FROM cases WHERE {rw}created_at LIKE ?",
        params + [prefix],
    ).fetchone()["s"]
    case_by_progress = db.execute(
        f"SELECT progress, COUNT(*) c FROM cases WHERE {rw}created_at LIKE ? "
        "GROUP BY progress", params + [prefix],
    ).fetchall()
    case_by_team = db.execute(
        f"SELECT team, COUNT(*) c FROM cases WHERE {rw}created_at LIKE ? "
        "GROUP BY team", params + [prefix],
    ).fetchall()

    return render_template(
        "stats.html", month=month,
        total=total, closed=closed,
        rate=round(closed / total * 100, 1) if total else 0,
        by_team=by_team, by_category=by_category,
        by_person=by_person, by_community=by_community,
        case_total=case_total, case_fine=case_fine,
        case_by_progress=case_by_progress, case_by_team=case_by_team,
        progress_labels=PROGRESS_LABELS, user=current_user(),
    )


# ---------- 导出（Excel 文字 + 原图打包 ZIP） ----------
@app.route("/export")
@require_user
def export_data():
    from openpyxl import Workbook
    import zipfile as _zip

    team = request.args.get("team", "")
    category = request.args.get("category", "")
    reporter = request.args.get("reporter", "")
    status = request.args.get("status", "")
    month = request.args.get("month", "")
    community = request.args.get("community", "")
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()

    where, params = scope_where()
    if team:
        where = (where + " AND " if where else "") + "team = ?"
        params.append(team)
    if category:
        where = (where + " AND " if where else "") + "category = ?"
        params.append(category)
    if reporter:
        where = (where + " AND " if where else "") + "reporter = ?"
        params.append(reporter)
    if status in ("pending", "closed"):
        where = (where + " AND " if where else "") + "status = ?"
        params.append(status)
    if month:
        where = (where + " AND " if where else "") + "created_at LIKE ?"
        params.append(month + "%")
    if community:
        where = (where + " AND " if where else "") + "community = ?"
        params.append(community)
    if start:
        where = (where + " AND " if where else "") + "created_at >= ?"
        params.append(start + " 00:00")
    if end:
        where = (where + " AND " if where else "") + "created_at <= ?"
        params.append(end + " 23:59")

    sql = "SELECT * FROM records"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY id"
    records = [dict(r) for r in get_db().execute(sql, params).fetchall()]

    # 按问题分类分组排序（按 CATEGORIES 既定顺序，组内按小区+时间）
    order = {c: i for i, c in enumerate(CATEGORIES)}
    records.sort(key=lambda r: (order.get(r["category"], 99),
                                r["community"], r["id"]))

    headers = ["序号", "所属中队", "小区", "问题分类", "问题描述", "状态",
               "上报人", "上报时间", "销号时间", "处理结果", "照片张数"]
    widths = [6, 20, 18, 14, 44, 8, 10, 17, 17, 30, 8]

    def photo_count(rid):
        return get_db().execute(
            "SELECT COUNT(*) c FROM images WHERE record_id=?", (rid,)
        ).fetchone()["c"]

    def row_of(i, r):
        return [i, r["team"], r["community"], r["category"], r["description"],
                "已办结" if r["status"] == "closed" else "待处理",
                r["reporter"], r["created_at"], r["closed_at"] or "",
                r["result"] or "", photo_count(r["id"])]

    def fill_sheet(ws, rows):
        ws.append(headers)
        for row in rows:
            ws.append(row)
        for col, w in enumerate(widths, 1):
            ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = w
        ws.freeze_panes = "A2"

    wb = Workbook()
    # 汇总表
    fill_sheet(wb.active, [row_of(i, r) for i, r in enumerate(records, 1)])
    wb.active.title = "汇总"
    # 每个问题分类一个工作表（只建非空分类）
    by_cat = {}
    for r in records:
        by_cat.setdefault(r["category"], []).append(r)
    for cat in CATEGORIES:
        if cat not in by_cat:
            continue
        ws = wb.create_sheet(title=cat)
        fill_sheet(ws, [row_of(i, r) for i, r in enumerate(by_cat[cat], 1)])

    buf = io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        xbuf = io.BytesIO()
        wb.save(xbuf)
        label = f"巡查记录_{month or '全部'}.xlsx"
        z.writestr(label, xbuf.getvalue())
        for i, r in enumerate(records, 1):
            imgs = get_db().execute(
                "SELECT * FROM images WHERE record_id=? ORDER BY id", (r["id"],)
            ).fetchall()
            if not imgs:
                continue
            for j, img in enumerate(imgs, 1):
                p = UPLOADS_DIR / img["filepath"]
                if not p.exists():
                    continue
                typ = "现场" if img["type"] == "before" else "整改"
                z.write(p, f"照片/{r['category']}/{i}_{r['community']}_{typ}{j}.jpg")
    buf.seek(0)
    range_label = month or (f"{start}_to_{end}" if start or end else "全部")
    fname = f"巡查导出_{range_label}.zip".replace("/", "-")
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/zip")


# ---------- 图文 Word 导出 ----------
def _export_filters():
    """解析导出通用筛选参数，返回 (where, params, start, end, month)。"""
    team = request.args.get("team", "")
    category = request.args.get("category", "")
    reporter = request.args.get("reporter", "")
    status = request.args.get("status", "")
    month = request.args.get("month", "")
    community = request.args.get("community", "")
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    where, params = scope_where()
    for cond, p in [
        (team, ("team = ?", team)),
        (category, ("category = ?", category)),
        (reporter, ("reporter = ?", reporter)),
        (status if status in ("pending", "closed") else "", ("status = ?", status)),
    ]:
        if cond:
            where = (where + " AND " if where else "") + p[0]
            params.append(p[1])
    if month:
        where = (where + " AND " if where else "") + "created_at LIKE ?"
        params.append(month + "%")
    if community:
        where = (where + " AND " if where else "") + "community = ?"
        params.append(community)
    if start:
        where = (where + " AND " if where else "") + "created_at >= ?"
        params.append(start + " 00:00")
    if end:
        where = (where + " AND " if where else "") + "created_at <= ?"
        params.append(end + " 23:59")
    return where, params, start, end, month


@app.route("/export/doc")
@require_user
def export_doc():
    from docx import Document
    from docx.shared import Cm, Pt

    where, params, start, end, month = _export_filters()
    sql = "SELECT * FROM records"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY category, community, id"
    records = [dict(r) for r in get_db().execute(sql, params).fetchall()]

    doc = Document()
    doc.add_heading("社区城管巡查记录台账", 0)
    rng = month or (f"{start} ~ {end}" if start or end else "全部")
    doc.add_paragraph(f"导出范围：{rng}    共 {len(records)} 条记录")
    if not records:
        doc.add_paragraph("（无记录）")
    for i, r in enumerate(records, 1):
        doc.add_heading(f"{i}. {r['community'] or '未填小区'} · {r['category']}", level=1)
        doc.add_paragraph(
            f"所属中队：{r['team']}    上报人：{r['reporter']}\n"
            f"发现时间：{r['created_at']}"
            + (f"    销号时间：{r['closed_at']}" if r["closed_at"] else "")
        )
        doc.add_paragraph(f"问题描述：{r['description'] or '（未填写）'}")
        if r["status"] == "closed":
            doc.add_paragraph(f"处理结果：{r['result'] or '（未填写）'}")
        imgs = get_db().execute(
            "SELECT * FROM images WHERE record_id=? ORDER BY id", (r["id"],)
        ).fetchall()
        before = [x for x in imgs if x["type"] == "before"]
        after = [x for x in imgs if x["type"] == "after"]
        for label, group in (("现场照片", before), ("整改后照片", after)):
            if not group:
                continue
            doc.add_paragraph(label + f"（{len(group)} 张）：")
            for img in group:
                p = UPLOADS_DIR / img["filepath"]
                if p.exists():
                    try:
                        doc.add_picture(str(p), width=Cm(14))
                        doc.paragraphs[-1].alignment = 1
                    except Exception:
                        doc.add_paragraph("（图片无法加载）")
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    fname = f"巡查台账_{rng}.docx".replace("/", "-")
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ---------- 小区摸排台账导出（预览页 + Excel） ----------
LEDGER_DEPS = [
    ("ledger_report_dept", "问题上报部门", "县城管局"),
    ("ledger_lead_dept", "牵头部门", "县城市管理综合行政执法大队"),
    ("ledger_assist_dept", "配合部门", "社区、物业"),
]


def _ledger_groups():
    """摸排台账数据：按小区分组，每小区返回序号共用行 + 整改前/后照片路径。"""
    where, params, start, end, month = _export_filters()
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
        recs = sorted(by_comm[comm],
                      key=lambda r: (order.get(r["category"], 99), r["id"]))
        rows = []
        num, last_cat = 0, None
        before, after = [], []
        for r in recs:
            if r["category"] != last_cat:
                num += 1
                last_cat = r["category"]
            rows.append((num, r))
            for im in get_db().execute(
                "SELECT * FROM images WHERE record_id=? ORDER BY id",
                (r["id"],),
            ).fetchall():
                (before if im["type"] == "before" else after).append(
                    im["filepath"])
        groups.append({
            "community": comm, "rows": rows,
            "before": before, "after": after,
            "before_thumbs": [thumb_of(p) for p in before],
            "after_thumbs": [thumb_of(p) for p in after],
        })
    return groups, start, end


@app.route("/export/ledger")
@require_user
def export_ledger():
    groups, start, end = _ledger_groups()
    deps = {k: get_setting(k, d) or d for k, _l, d in LEDGER_DEPS}
    return render_template("ledger.html", groups=groups, deps=deps,
                           start=start, end=end, user=current_user())


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
        start=request.args.get("start", ""), end=request.args.get("end", "")))


@app.route("/export/ledger.xlsx")
@require_user
def export_ledger_xlsx():
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.drawing.image import Image as XLImage
    from PIL import Image as PILImage

    groups, start, end = _ledger_groups()
    deps = [get_setting(k, d) or d for k, _l, d in LEDGER_DEPS]
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    title_font = Font(name="黑体", size=16)
    head_font = Font(name="黑体", size=11)

    headers = ["序号", "问题上报部门", "牵头部门", "配合部门",
               "问题描述", "整改举措", "整改时限", "整改情况", "备注"]
    widths = [7.4, 14.6, 21.1, 14.4, 20.8, 21.1, 14.6, 13.1, 9.1]

    def sheet_name(name):
        base = re.sub(r"[\[\]:*?/\\]", "", name)[:31] or "小区"
        if base not in used:
            used.add(base)
            return base
        for i in range(2, 100):
            cand = f"{base[:29]}{i}"
            if cand not in used:
                used.add(cand)
                return cand
        return base + "_x"

    def embed_photo(filepath, target_w=380):
        p = UPLOADS_DIR / filepath
        if not p.exists():
            return None
        try:
            im = PILImage.open(p)
            w, h = im.size
            if w > target_w:
                h = int(h * target_w / w)
                im = im.resize((target_w, h))
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=85)
            buf.seek(0)
            img = XLImage(buf)
            img.width, img.height = im.size
            return img
        except Exception:
            return None

    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    if not groups:  # 无数据时也要有一张可见表，否则保存报错
        ws = wb.create_sheet(title="无数据")
        ws["A1"] = "当前范围内没有巡查记录"
        ws["A1"].font = title_font
    for g in groups:
        ws = wb.create_sheet(title=sheet_name(g["community"]))
        ws.merge_cells("A1:I1")
        c = ws.cell(row=1, column=1, value=f"{g['community']}小区摸排情况")
        c.font = title_font
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 30
        for col, (h, w) in enumerate(zip(headers, widths), 1):
            cell = ws.cell(row=2, column=col, value=h)
            cell.font = head_font
            cell.border = border
            cell.alignment = center
            ws.column_dimensions[cell.column_letter].width = w
        r = 3
        for num, rec in g["rows"]:
            vals = [num, deps[0], deps[1], deps[2],
                    rec["description"] or rec["category"],
                    rec["result"] or "",
                    rec["deadline"] or "",
                    "已整改" if rec["status"] == "closed" else "未整改",
                    ""]
            for col, v in enumerate(vals, 1):
                cell = ws.cell(row=r, column=col, value=v)
                cell.border = border
                cell.alignment = left if col in (5, 6) else center
            r += 1
        if g["before"] or g["after"]:
            r += 1  # 空一行
            lab = ws.cell(row=r, column=1, value="整改前")
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
            lab2 = ws.cell(row=r, column=6, value="整改后")
            ws.merge_cells(start_row=r, start_column=6, end_row=r, end_column=9)
            lab.font = lab2.font = head_font
            lab.alignment = lab2.alignment = center
            r += 1
            for i in range(max(len(g["before"]), len(g["after"]))):
                hmax = 0
                if i < len(g["before"]):
                    img = embed_photo(g["before"][i])
                    if img:
                        ws.add_image(img, f"A{r}")
                        hmax = max(hmax, img.height * 0.75)
                if i < len(g["after"]):
                    img = embed_photo(g["after"][i])
                    if img:
                        ws.add_image(img, f"F{r}")
                        hmax = max(hmax, img.height * 0.75)
                ws.row_dimensions[r].height = max(hmax + 6, 30)
                r += 1
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    rng = f"{start}_to_{end}" if start or end else "全部"
    fname = f"小区摸排台账_{rng}.xlsx".replace("/", "-")
    log_action("导出摸排台账", f"{rng} · {len(groups)} 个小区")
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------- 数据一键备份（主管理员） ----------
@app.route("/admin/backup")
@require_user
def admin_backup():
    u = current_user()
    if not u or u["role"] != "super":
        abort(403)
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
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/zip")


# ---------- 辅助接口 ----------
@app.route("/api/communities")
@require_user
def api_communities():
    q = request.args.get("q", "").strip()
    return jsonify(community_autocomplete(q))


@app.route("/uploads/<path:filepath>")
def uploads(filepath):
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
    app.run(host="0.0.0.0", port=5000, debug=True)
