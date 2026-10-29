"""构造一个小型电商数据库 shop.db（SQLite），作为 Text-to-SQL 微调的“业务库”。

用法：python build_db.py            # 在当前目录生成 shop.db
数据由固定随机种子生成，任何人运行得到的数据库都完全一样，便于复现评测结果。
"""
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE customers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,          -- 客户姓名
    city        TEXT NOT NULL,          -- 所在城市
    signup_date TEXT NOT NULL           -- 注册日期，格式 YYYY-MM-DD
);

CREATE TABLE products (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,           -- 商品名称
    category   TEXT NOT NULL,           -- 品类
    price_cent INTEGER NOT NULL         -- 当前标价，单位：分
);

CREATE TABLE orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date  TEXT NOT NULL,          -- 下单日期，格式 YYYY-MM-DD
    status      TEXT NOT NULL           -- 订单状态：paid / shipped / completed / cancelled / refunded
);

CREATE TABLE order_items (
    id              INTEGER PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id),
    product_id      INTEGER NOT NULL REFERENCES products(id),
    quantity        INTEGER NOT NULL,   -- 购买数量
    unit_price_cent INTEGER NOT NULL    -- 成交单价，单位：分
);
""".strip()

CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京"]
SURNAMES = "王李张刘陈杨黄赵周吴徐孙马朱胡郭何林罗高"
GIVEN = ["伟", "芳", "娜", "敏", "静", "磊", "洋", "勇", "艳", "杰", "娟", "涛", "明", "超", "霞",
         "平", "刚", "桂英", "建华", "晓东", "思远", "雨桐", "子涵", "浩然", "欣怡"]

PRODUCTS = {
    "数码": [("无线蓝牙耳机", 19900), ("机械键盘", 34900), ("移动电源", 12900), ("智能手表", 129900),
             ("降噪耳机", 89900), ("平板电脑", 299900), ("USB-C 扩展坞", 25900)],
    "家居": [("记忆棉枕头", 15900), ("全棉四件套", 39900), ("香薰蜡烛", 6900), ("收纳箱", 4900),
             ("落地灯", 29900), ("电动牙刷", 19900)],
    "食品": [("坚果礼盒", 12800), ("挂耳咖啡", 5900), ("有机大米", 8900), ("黑巧克力", 3900),
             ("牛肉干", 4500), ("燕麦片", 3200), ("绿茶", 9900)],
    "服饰": [("纯棉T恤", 9900), ("牛仔裤", 25900), ("羽绒服", 69900), ("运动鞋", 45900),
             ("羊毛围巾", 15900), ("双肩包", 21900)],
    "图书": [("Python 编程入门", 7900), ("深度学习", 16800), ("三体", 9300), ("活着", 3500),
             ("算法导论", 12800), ("人类简史", 6800)],
    "美妆": [("保湿面霜", 26900), ("防晒霜", 15900), ("洗面奶", 7900), ("口红", 22000),
             ("精华液", 59900)],
}

STATUSES = ["paid", "shipped", "completed", "cancelled", "refunded"]
STATUS_WEIGHTS = [10, 15, 55, 12, 8]


def build(db_path: Path) -> None:
    rng = random.Random(20250228)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # 客户：200 位，注册时间分布在 2023 年到 2024 年
    used_names = set()
    customers = []
    for cid in range(1, 201):
        while True:
            name = rng.choice(SURNAMES) + rng.choice(GIVEN)
            if name not in used_names:
                used_names.add(name)
                break
        signup = date(2023, 1, 1) + timedelta(days=rng.randrange(0, 700))
        customers.append((cid, name, rng.choice(CITIES), signup.isoformat()))
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)

    # 商品
    products = []
    pid = 1
    for cat, items in PRODUCTS.items():
        for pname, price in items:
            products.append((pid, pname, cat, price))
            pid += 1
    conn.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    # 订单：2024 全年 3000 单；约 15% 的客户从未下单
    buyers = [c for c in customers if rng.random() > 0.15]
    orders, items = [], []
    item_id = 1
    for oid in range(1, 3001):
        cust = rng.choice(buyers)
        signup = date.fromisoformat(cust[3])
        start = max(signup, date(2024, 1, 1))
        if start > date(2024, 12, 31):
            start = date(2024, 12, 31)
        odate = start + timedelta(days=rng.randrange(0, (date(2024, 12, 31) - start).days + 1))
        status = rng.choices(STATUSES, STATUS_WEIGHTS)[0]
        orders.append((oid, cust[0], odate.isoformat(), status))
        for prod in rng.sample(products, rng.choice([1, 1, 1, 2, 2, 3, 4])):
            qty = rng.choice([1, 1, 1, 1, 2, 2, 3, 5])
            # 成交价 = 标价打一个随机折扣（体现“标价”和“成交价”是两个字段）
            unit = int(prod[3] * rng.choice([1.0, 1.0, 1.0, 0.9, 0.85, 0.8]))
            items.append((item_id, oid, prod[0], qty, unit))
            item_id += 1
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("shop.db")
    build(out)
    print(f"已生成 {out}")
