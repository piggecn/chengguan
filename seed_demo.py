# -*- coding: utf-8 -*-
"""灌入样板数据（人员+记录+案件+投诉，无照片）。用后可删脚本，数据按用户要求保留。"""
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "records.db")

USERS = [
    # name, unit, role
    ("办公室主任", "办公室", "office"),
    ("张中队长", "鄱阳镇社区一中队", "captain"),
    ("李副队长", "鄱阳镇社区一中队", "vice-captain"),
    ("王队员", "鄱阳镇社区一中队", "member"),
    ("刘队员", "鄱阳镇社区一中队", "member"),
    ("陈中队长", "鄱阳镇社区二中队", "captain"),
    ("周队员", "鄱阳镇社区二中队", "member"),
    ("吴中队长", "饶州街道社区一中队", "captain"),
    ("郑队员", "饶州街道社区一中队", "member"),
    ("孙中队长", "饶州街道社区二中队", "captain"),
    ("冯队员", "饶州街道社区二中队", "member"),
]

RECORDS = [
    # team, community, category, description, status, result, reporter, created, closed
    ("鄱阳镇社区一中队", "东投太阳城", "牛皮癣小广告", "3栋单元门贴满开锁小广告", "pending", "", "王队员", "2026-09-01 09:30", None),
    ("鄱阳镇社区一中队", "阳光花园", "电动车乱停放", "小区南门电动车横七竖八堵住消防通道", "pending", "", "李副队长", "2026-08-28 15:10", None),
    ("鄱阳镇社区一中队", "东湖名邸", "出店经营", "沿街水果店货物摆出店门占用人行道", "closed", "已劝导入店经营，人行道恢复畅通", "张中队长", "2026-08-20 10:00", "2026-08-22 16:30"),
    ("鄱阳镇社区二中队", "滨江国际城", "流动摊贩", "北门流动早餐摊聚集，油污满地", "pending", "", "周队员", "2026-09-02 08:45", None),
    ("鄱阳镇社区二中队", "紫金花园", "毁坏绿化", "业主圈占绿地种菜，毁坏灌木约10平米", "closed", "已责令恢复绿化，补种完成", "陈中队长", "2026-08-25 11:20", "2026-08-30 09:00"),
    ("鄱阳镇社区二中队", "湖城天悦", "违法搭建", "顶层违规搭建阳光房约15平米", "pending", "", "陈中队长", "2026-09-03 14:00", None),
    ("饶州街道社区一中队", "学府名苑", "乱堆放杂物", "楼道口堆放旧家具和纸箱", "pending", "", "郑队员", "2026-09-03 16:20", None),
    ("饶州街道社区一中队", "永兴农贸周边", "占道经营", "市场外围摊占道，早高峰通行受阻", "closed", "已划线规范经营区域，安排专人值守", "吴中队长", "2026-08-22 07:50", "2026-08-26 17:00"),
    ("饶州街道社区二中队", "锦绣家园", "噪音扰民", "夜间烧烤摊音响噪音扰民", "pending", "", "冯队员", "2026-09-04 21:30", None),
    ("饶州街道社区二中队", "芝山路沿街", "乱倒垃圾", "绿化带内倾倒建筑垃圾两处", "pending", "", "孙中队长", "2026-09-05 09:00", None),
    ("鄱阳镇社区一中队", "鄱阳湖大道沿线", "破坏市政设施", "公交站台玻璃被砸碎一块", "pending", "", "王队员", "2026-09-05 16:00", None),
    ("饶州街道社区一中队", "五一中心学校周边", "流动摊贩", "放学时段流动摊贩围校门口", "pending", "", "吴中队长", "2026-09-06 16:40", None),
]

CASES = [
    ("鄱阳镇社区一中队", "占用公共空间堆放物料案", "filed", 500, "张中队长", "2026-08-24 10:00"),
    ("鄱阳镇社区一中队", "擅自设置户外广告案", "investigating", 0, "张中队长", "2026-09-01 11:00"),
    ("鄱阳镇社区二中队", "擅自倾倒建筑垃圾案", "investigating", 0, "陈中队长", "2026-08-29 15:30"),
    ("饶州街道社区一中队", "占道经营影响通行案", "closed", 150, "吴中队长", "2026-08-18 09:00"),
    ("饶州街道社区二中队", "未密闭运输渣土案", "filed", 800, "孙中队长", "2026-09-04 10:30"),
    ("鄱阳镇社区二中队", "违规饲养家禽案", "closed", 200, "周队员", "2026-08-26 14:00"),
]

COMPLAINTS = [
    ("鄱阳镇社区一中队", "东投太阳城", "13800000001", "2号楼商户高音喇叭整天放广告", "done", "王队员", "王队员", "2026-08-30 10:00", "2026-09-01 09:00"),
    ("鄱阳镇社区一中队", "东湖名邸", "13800000002", "3栋楼下电动车乱停放堵路", "pending", "刘队员", "", "2026-09-02 08:30", None),
    ("鄱阳镇社区二中队", "滨江国际城", "13800000003", "小区门口烧烤摊油烟直排楼上", "pending", "周队员", "", "2026-09-03 19:00", None),
    ("饶州街道社区一中队", "学府名苑", "13800000004", "隔壁餐馆半夜倾倒泔水异味大", "pending", "郑队员", "", "2026-09-04 07:40", None),
    ("饶州街道社区二中队", "锦绣家园", "13800000005", "有人毁绿种菜还装地锁", "done", "冯队员", "冯队员", "2026-08-27 09:10", "2026-08-31 15:00"),
]

COMMUNITIES = ["东投太阳城", "阳光花园", "东湖名邸", "滨江国际城", "紫金花园",
               "湖城天悦", "学府名苑", "永兴农贸周边", "锦绣家园", "芝山路沿街",
               "鄱阳湖大道沿线", "五一中心学校周边"]

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

db.execute("DELETE FROM users WHERE name != '主管理员'")
for name, unit, role in USERS:
    if db.execute("SELECT 1 FROM users WHERE unit=? AND name=?", (unit, name)).fetchone():
        continue
    db.execute("INSERT INTO users(name, unit, role, pin, created_at) VALUES(?,?,?,?,?)",
               (name, unit, role, "0000", "2026-09-01 08:00"))

db.execute("DELETE FROM records")
db.execute("DELETE FROM images")
db.execute("DELETE FROM cases")
db.execute("DELETE FROM complaints")
db.execute("DELETE FROM communities")
for team, community, cat, desc, status, result, reporter, created, closed in RECORDS:
    db.execute(
        "INSERT INTO records(team, community, category, description, status, "
        "result, reporter, created_at, closed_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (team, community, cat, desc, status, result, reporter, created, closed),
    )
for team, name, progress, fine, reporter, created in CASES:
    db.execute(
        "INSERT INTO cases(team, case_name, progress, fine_amount, reporter, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
        (team, name, progress, fine, reporter, created, created),
    )
for team, community, phone, content, status, reporter, handler, created, handled in COMPLAINTS:
    db.execute(
        "INSERT INTO complaints(team, community, phone, content, status, "
        "reporter, handler, created_at, handled_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (team, community, phone, content, status, reporter, handler, created, handled),
    )
for c in COMMUNITIES:
    db.execute("INSERT INTO communities(name, count) VALUES(?,1)", (c,))

db.commit()
print("人员:", db.execute("SELECT COUNT(*) FROM users").fetchone()[0])
print("记录:", db.execute("SELECT COUNT(*) FROM records").fetchone()[0])
print("案件:", db.execute("SELECT COUNT(*) FROM cases").fetchone()[0])
print("投诉:", db.execute("SELECT COUNT(*) FROM complaints").fetchone()[0])
db.close()
