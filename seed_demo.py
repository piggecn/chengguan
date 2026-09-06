# -*- coding: utf-8 -*-
"""灌入样板数据（记录+案件+投诉，无照片）。仅用于开发/测试。

⚠️ 会先清空 records/images/cases/complaints/communities 表，只在你自己的测试库上跑。
账号由 app.py 首次初始化自动创建，这里不动 users 表。
"""
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
# 跟 app.py 一致：DATA_DIR 优先，默认 ./data
DATA = os.environ.get("DATA_DIR") or os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)
DB = os.path.join(DATA, "records.db")

REPORTER = "主账号"

RECORDS = [
    # town, community, category, description, status, result, created, closed
    ("鄱阳镇", "东投太阳城", "牛皮癣小广告", "3栋单元门贴满开锁小广告", "pending", "", "2026-09-01 09:30", None),
    ("鄱阳镇", "阳光花园", "电动车乱停放", "小区南门电动车横七竖八堵住消防通道", "pending", "", "2026-08-28 15:10", None),
    ("鄱阳镇", "东湖名邸", "出店经营", "沿街水果店货物摆出店门占用人行道", "closed", "已劝导入店经营，人行道恢复畅通", "2026-08-20 10:00", "2026-08-22 16:30"),
    ("鄱阳镇", "滨江国际城", "流动摊贩", "北门流动早餐摊聚集，油污满地", "pending", "", "2026-09-02 08:45", None),
    ("鄱阳镇", "紫金花园", "毁坏绿化", "业主圈占绿地种菜，毁坏灌木约10平米", "closed", "已责令恢复绿化，补种完成", "2026-08-25 11:20", "2026-08-30 09:00"),
    ("鄱阳镇", "湖城天悦", "违法搭建", "顶层违规搭建阳光房约15平米", "pending", "", "2026-09-03 14:00", None),
    ("饶州街道", "学府名苑", "乱堆放杂物", "楼道口堆放旧家具和纸箱", "pending", "", "2026-09-03 16:20", None),
    ("饶州街道", "永兴农贸周边", "占道经营", "市场外围摊占道，早高峰通行受阻", "closed", "已划线规范经营区域，安排专人值守", "2026-08-22 07:50", "2026-08-26 17:00"),
    ("饶州街道", "锦绣家园", "噪音扰民", "夜间烧烤摊音响噪音扰民", "pending", "", "2026-09-04 21:30", None),
    ("饶州街道", "芝山路沿街", "乱倒垃圾", "绿化带内倾倒建筑垃圾两处", "pending", "", "2026-09-05 09:00", None),
    ("鄱阳镇", "鄱阳湖大道沿线", "破坏市政设施", "公交站台玻璃被砸碎一块", "pending", "", "2026-09-05 16:00", None),
    ("饶州街道", "五一中心学校周边", "流动摊贩", "放学时段流动摊贩围校门口", "pending", "", "2026-09-06 16:40", None),
]

CASES = [
    ("鄱阳镇", "占用公共空间堆放物料案", "filed", 500, "2026-08-24 10:00"),
    ("鄱阳镇", "擅自设置户外广告案", "investigating", 0, "2026-09-01 11:00"),
    ("鄱阳镇", "擅自倾倒建筑垃圾案", "investigating", 0, "2026-08-29 15:30"),
    ("饶州街道", "占道经营影响通行案", "closed", 150, "2026-08-18 09:00"),
    ("饶州街道", "未密闭运输渣土案", "filed", 800, "2026-09-04 10:30"),
    ("鄱阳镇", "违规饲养家禽案", "closed", 200, "2026-08-26 14:00"),
]

COMPLAINTS = [
    ("鄱阳镇", "东投太阳城", "13800000001", "2号楼商户高音喇叭整天放广告", "done", REPORTER, REPORTER, "2026-08-30 10:00", "2026-09-01 09:00"),
    ("鄱阳镇", "东湖名邸", "13800000002", "3栋楼下电动车乱停放堵路", "pending", REPORTER, "", "2026-09-02 08:30", None),
    ("鄱阳镇", "滨江国际城", "13800000003", "小区门口烧烤摊油烟直排楼上", "pending", REPORTER, "", "2026-09-03 19:00", None),
    ("饶州街道", "学府名苑", "13800000004", "隔壁餐馆半夜倾倒泔水异味大", "pending", REPORTER, "", "2026-09-04 07:40", None),
    ("饶州街道", "锦绣家园", "13800000005", "有人毁绿种菜还装地锁", "done", REPORTER, REPORTER, "2026-08-27 09:10", "2026-08-31 15:00"),
]

COMMUNITIES = ["东投太阳城", "阳光花园", "东湖名邸", "滨江国际城", "紫金花园",
               "湖城天悦", "学府名苑", "永兴农贸周边", "锦绣家园", "芝山路沿街",
               "鄱阳湖大道沿线", "五一中心学校周边"]

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

db.execute("DELETE FROM images")
db.execute("DELETE FROM records")
db.execute("DELETE FROM cases")
db.execute("DELETE FROM complaints")
db.execute("DELETE FROM communities")

for town, community, cat, desc, status, result, created, closed in RECORDS:
    db.execute(
        "INSERT INTO records(town, community, category, description, status, "
        "result, reporter, created_at, closed_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (town, community, cat, desc, status, result, REPORTER, created, closed),
    )
for town, name, progress, fine, created in CASES:
    db.execute(
        "INSERT INTO cases(town, case_name, progress, fine_amount, reporter, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
        (town, name, progress, fine, REPORTER, created, created),
    )
for town, community, phone, content, status, reporter, handler, created, handled in COMPLAINTS:
    db.execute(
        "INSERT INTO complaints(team, community, phone, content, status, "
        "reporter, handler, created_at, handled_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (town, community, phone, content, status, reporter, handler, created, handled),
    )
for c in COMMUNITIES:
    db.execute("INSERT INTO communities(name, count) VALUES(?,1)", (c,))

db.commit()
print("记录:", db.execute("SELECT COUNT(*) FROM records").fetchone()[0])
print("案件:", db.execute("SELECT COUNT(*) FROM cases").fetchone()[0])
print("投诉:", db.execute("SELECT COUNT(*) FROM complaints").fetchone()[0])
print("乡镇分布:", {r[0]: r[1] for r in db.execute(
    "SELECT town, COUNT(*) FROM records GROUP BY town")})
db.close()
