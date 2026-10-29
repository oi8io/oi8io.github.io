"""合成 Text-to-SQL 训练 / 测试数据。

思路（文章“数据从哪来”一节）：
  1. 人工写“种子”：每个模板 = 若干种问法 + 一条标准 SQL（带参数槽位）
  2. 程序批量填参数，放大成上千条样本
  3. 每条 SQL 都在 shop.db 上真实执行一遍，执行失败、结果为空或排名有并列的样本直接丢弃
  4. 按“模板”划分出一份分布外测试集（test_ood），检验模型是真学会了口径，还是只背住了句式

输出（LLaMA-Factory 的 alpaca 格式，带 system 字段）：
  data/text2sql_train.json   训练集
  data/text2sql_test_id.json 同分布测试集：模板见过，具体问题没见过
  data/text2sql_test_ood.json 分布外测试集：模板完全没见过
"""
import json
import random
import sqlite3
from pathlib import Path

from build_db import build
from common import SYSTEM_PROMPT, build_user_prompt

HERE = Path(__file__).parent
VALID = "o.status IN ('paid', 'shipped', 'completed')"
SALES = "ROUND(SUM(oi.quantity * oi.unit_price_cent) / 100.0, 2)"
CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京"]
CATS = ["数码", "家居", "食品", "服饰", "图书", "美妆"]
QUARTERS = {1: ("2024-01-01", "2024-03-31"), 2: ("2024-04-01", "2024-06-30"),
            3: ("2024-07-01", "2024-09-30"), 4: ("2024-10-01", "2024-12-31")}


def month_params(rng):
    m = rng.randint(1, 12)
    return {"y": 2024, "m": m, "ym": f"2024-{m:02d}"}


# 每个模板：qs = 多种问法；sql = 标准答案；params = 参数生成函数；ordered = 结果是否要求有序；rank = 是否是 TopN 排名
# 写模板时刻意保持同一种 SQL 风格（固定别名 o / oi / p / c），风格统一的示范更容易学。
TEMPLATES = {
    # ---------------- 同分布模板（训练 + test_id） ----------------
    "city_customers": dict(
        qs=["{city}有多少位客户？", "来自{city}的客户一共有几个？", "统计一下{city}的客户数量"],
        sql="SELECT COUNT(*) FROM customers WHERE city = '{city}';",
        params=lambda r: {"city": r.choice(CITIES)}),
    "category_products": dict(
        qs=["{cat}品类下有多少种商品？", "我们卖的{cat}类商品有几款？", "{cat}这个品类一共上架了多少个商品"],
        sql="SELECT COUNT(*) FROM products WHERE category = '{cat}';",
        params=lambda r: {"cat": r.choice(CATS)}),
    "month_orders": dict(
        qs=["{y}年{m}月一共有多少笔订单？", "{ym} 的订单量是多少？", "帮我数一下{y}年{m}月的下单数"],
        sql=f"SELECT COUNT(*) FROM orders o WHERE {VALID} AND strftime('%Y-%m', o.order_date) = '{{ym}}';",
        params=month_params),
    "month_sales": dict(
        qs=["{y}年{m}月的销售额是多少？", "{y}年{m}月营收多少钱？", "{ym} 的 GMV 是多少"],
        sql=(f"SELECT {SALES} FROM orders o JOIN order_items oi ON o.id = oi.order_id "
             f"WHERE {VALID} AND strftime('%Y-%m', o.order_date) = '{{ym}}';"),
        params=month_params),
    "category_sales_quarter": dict(
        qs=["{y}年第{q}季度{cat}品类卖了多少钱？", "{cat}类商品在{y}年Q{q}的销售额是多少", "统计{y}年第{q}季度{cat}的营收"],
        sql=(f"SELECT {SALES} FROM orders o JOIN order_items oi ON o.id = oi.order_id "
             "JOIN products p ON oi.product_id = p.id "
             f"WHERE {VALID} AND p.category = '{{cat}}' AND o.order_date BETWEEN '{{start}}' AND '{{end}}';"),
        params=lambda r: (lambda q: {"y": 2024, "q": q, "cat": r.choice(CATS),
                                     "start": QUARTERS[q][0], "end": QUARTERS[q][1]})(r.randint(1, 4))),
    "top_products_qty": dict(
        qs=["{y}年销量最高的{n}个商品是哪些？", "按卖出的件数算，{y}年排名前{n}的商品", "{y}年卖得最多的前{n}款商品是什么",
            "{y}年最畅销的{n}个商品", "列出{y}年销量 Top{n} 的商品", "{y}年哪{n}个商品卖出去的件数最多"],
        sql=("SELECT p.name, SUM(oi.quantity) AS total_qty FROM orders o "
             "JOIN order_items oi ON o.id = oi.order_id JOIN products p ON oi.product_id = p.id "
             f"WHERE {VALID} AND strftime('%Y', o.order_date) = '{{y}}' "
             "GROUP BY p.id, p.name ORDER BY total_qty DESC LIMIT {n};"),
        params=lambda r: {"y": 2024, "n": r.choice([3, 5, 10])}, rank=True),
    "top_customers_sales": dict(
        qs=["消费金额最高的前{n}位客户是谁？", "哪{n}个客户在我们这儿花钱最多？", "列出累计消费额排名前{n}的客户",
            "找出消费最多的{n}个大客户", "累计消费 Top{n} 的客户和他们的消费额", "给我花钱最多的{n}位客户名单"],
        sql=(f"SELECT c.name, {SALES} AS total FROM customers c JOIN orders o ON c.id = o.customer_id "
             f"JOIN order_items oi ON o.id = oi.order_id WHERE {VALID} "
             "GROUP BY c.id, c.name ORDER BY total DESC LIMIT {n};"),
        params=lambda r: {"n": r.choice([3, 5, 10])}, rank=True),
    "city_orders": dict(
        qs=["{city}的客户一共下了多少笔订单？", "{city}地区的订单数是多少", "统计{city}客户的下单量"],
        sql=(f"SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.id "
             f"WHERE {VALID} AND c.city = '{{city}}';"),
        params=lambda r: {"city": r.choice(CITIES)}),
    "category_sales_rank": dict(
        qs=["{y}年各品类的销售额分别是多少？按从高到低排", "按品类统计{y}年营收，从高到低", "{y}年哪个品类最赚钱？给出每个品类的销售额排行",
            "{y}年每个品类卖了多少钱，降序排列", "给我{y}年的品类销售额排行榜", "{y}年分品类的 GMV，从大到小", "对比一下{y}年各个品类的营收"],
        sql=(f"SELECT p.category, {SALES} AS sales FROM orders o JOIN order_items oi ON o.id = oi.order_id "
             f"JOIN products p ON oi.product_id = p.id WHERE {VALID} AND strftime('%Y', o.order_date) = '{{y}}' "
             "GROUP BY p.category ORDER BY sales DESC;"),
        params=lambda r: {"y": 2024}, ordered=True),
    "monthly_sales": dict(
        qs=["{y}年每个月的销售额是多少？", "按月列出{y}年的营收走势", "给我{y}年逐月的 GMV",
            "{y}年的月度销售额", "看一下{y}年每月营收的变化", "{y}年 1 到 12 月各月卖了多少钱", "按月份汇总{y}年的销售额"],
        sql=(f"SELECT strftime('%Y-%m', o.order_date) AS month, {SALES} AS sales FROM orders o "
             f"JOIN order_items oi ON o.id = oi.order_id WHERE {VALID} AND strftime('%Y', o.order_date) = '{{y}}' "
             "GROUP BY month ORDER BY month;"),
        params=lambda r: {"y": 2024}, ordered=True),
    "customer_all_orders": dict(
        qs=["{name}总共下过几次单？取消和退款的也算", "客户{name}的全部订单（含取消、退款）有多少笔", "不管什么状态，{name}一共有多少个订单"],
        sql="SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.id WHERE c.name = '{name}';",
        params=lambda r: {"name": r.choice(NAMES)}),
    "month_cancelled": dict(
        qs=["{y}年{m}月有多少订单被取消了？", "{ym} 取消的订单有几笔", "统计{y}年{m}月的取消单数量"],
        sql="SELECT COUNT(*) FROM orders o WHERE o.status = 'cancelled' AND strftime('%Y-%m', o.order_date) = '{ym}';",
        params=month_params),
    "month_refund_amount": dict(
        qs=["{y}年{m}月退款的金额一共多少？", "{ym} 退款单涉及多少钱", "算一下{y}年{m}月退掉的订单总金额"],
        sql=(f"SELECT {SALES} FROM orders o JOIN order_items oi ON o.id = oi.order_id "
             "WHERE o.status = 'refunded' AND strftime('%Y-%m', o.order_date) = '{ym}';"),
        params=month_params),
    "month_aov": dict(
        qs=["{y}年{m}月的平均客单价是多少？", "{ym} 每笔订单平均多少钱", "算一下{y}年{m}月的客单价"],
        sql=("SELECT ROUND(SUM(oi.quantity * oi.unit_price_cent) / 100.0 / COUNT(DISTINCT o.id), 2) "
             f"FROM orders o JOIN order_items oi ON o.id = oi.order_id WHERE {VALID} "
             "AND strftime('%Y-%m', o.order_date) = '{ym}';"),
        params=month_params),
    "never_ordered": dict(
        qs=["有多少客户从来没下过单？", "注册了但一单都没买过的客户有几个", "统计零订单的客户数量", "有哪些客户从没下单？给我个总数",
            "一笔订单都没有的客户数", "多少注册客户还没有任何订单记录", "沉睡客户（从未下单）有多少人"],
        sql="SELECT COUNT(*) FROM customers c WHERE c.id NOT IN (SELECT customer_id FROM orders);",
        params=lambda r: {}),
    "product_price": dict(
        qs=["{product}现在卖多少钱？", "{product}的标价是多少元", "查一下{product}的售价"],
        sql="SELECT price_cent / 100.0 FROM products WHERE name = '{product}';",
        params=lambda r: {"product": r.choice(PRODUCT_NAMES)}),
    "products_over_price": dict(
        qs=["标价超过{x}元的商品有几个？", "售价高于{x}块的商品有多少款", "价格在{x}元以上的商品数量"],
        sql="SELECT COUNT(*) FROM products WHERE price_cent > {cent};",
        params=lambda r: (lambda x: {"x": x, "cent": x * 100})(r.choice([50, 100, 200, 300, 500, 1000]))),
    "signup_month": dict(
        qs=["{y}年{m}月新注册了多少客户？", "{y}年{m}月有几个新用户注册", "{ym} 的注册客户数"],
        sql="SELECT COUNT(*) FROM customers WHERE strftime('%Y-%m', signup_date) = '{ym}';",
        params=lambda r: (lambda y, m: {"y": y, "m": m, "ym": f"{y}-{m:02d}"})(r.choice([2023, 2024]), r.randint(1, 12))),
    "product_sales": dict(
        qs=["{product}在{y}年一共卖了多少钱？", "{y}年{product}的销售额", "{product}全年（{y}年）营收多少"],
        sql=(f"SELECT {SALES} FROM orders o JOIN order_items oi ON o.id = oi.order_id "
             "JOIN products p ON oi.product_id = p.id "
             f"WHERE {VALID} AND p.name = '{{product}}' AND strftime('%Y', o.order_date) = '{{y}}';"),
        params=lambda r: {"product": r.choice(PRODUCT_NAMES), "y": 2024}),
}

# ---------------- 分布外模板（只出现在 test_ood） ----------------
# 每个模板都是训练里见过的“口径”的新组合，用来检验模型学到的是规则还是句式。
OOD_TEMPLATES = {
    "repeat_customers": dict(
        qs=["{y}年有多少客户下过至少两笔订单？", "{y}年的复购客户（下单两次及以上）有多少人", "统计{y}年订单数不少于 2 的客户数"],
        sql=(f"SELECT COUNT(*) FROM (SELECT o.customer_id FROM orders o WHERE {VALID} "
             "AND strftime('%Y', o.order_date) = '{y}' GROUP BY o.customer_id HAVING COUNT(*) >= 2);"),
        params=lambda r: {"y": 2024}),
    "city_month_sales": dict(
        qs=["{y}年{m}月{city}的客户贡献了多少销售额？"],
        sql=(f"SELECT {SALES} FROM orders o JOIN order_items oi ON o.id = oi.order_id "
             f"JOIN customers c ON o.customer_id = c.id WHERE {VALID} AND c.city = '{{city}}' "
             "AND strftime('%Y-%m', o.order_date) = '{ym}';"),
        params=lambda r: {**month_params(r), "city": r.choice(CITIES)}),
    "category_month_qty": dict(
        qs=["{y}年{m}月{cat}品类一共卖出去多少件？"],
        sql=("SELECT SUM(oi.quantity) FROM orders o JOIN order_items oi ON o.id = oi.order_id "
             f"JOIN products p ON oi.product_id = p.id WHERE {VALID} AND p.category = '{{cat}}' "
             "AND strftime('%Y-%m', o.order_date) = '{ym}';"),
        params=lambda r: {**month_params(r), "cat": r.choice(CATS)}),
    "top_city_sales": dict(
        qs=["{y}年销售额最高的城市是哪个？", "{y}年哪个城市的客户贡献营收最多", "{y}年 GMV 排第一的城市"],
        sql=("SELECT c.city FROM customers c JOIN orders o ON c.id = o.customer_id "
             f"JOIN order_items oi ON o.id = oi.order_id WHERE {VALID} AND strftime('%Y', o.order_date) = '{{y}}' "
             "GROUP BY c.city ORDER BY SUM(oi.quantity * oi.unit_price_cent) DESC LIMIT 1;"),
        params=lambda r: {"y": 2024}),
    "city_never_ordered": dict(
        qs=["{city}有多少客户从来没下过单？"],
        sql="SELECT COUNT(*) FROM customers c WHERE c.city = '{city}' AND c.id NOT IN (SELECT customer_id FROM orders);",
        params=lambda r: {"city": r.choice(CITIES)}),
    "customer_valid_sales": dict(
        qs=["客户{name}累计消费了多少钱？"],
        sql=(f"SELECT {SALES} FROM customers c JOIN orders o ON c.id = o.customer_id "
             f"JOIN order_items oi ON o.id = oi.order_id WHERE {VALID} AND c.name = '{{name}}';"),
        params=lambda r: {"name": r.choice(BUYER_NAMES)}),
}

NAMES: list[str] = []
BUYER_NAMES: list[str] = []
PRODUCT_NAMES: list[str] = []


def check(conn, tpl, sql):
    """执行校验：能跑、结果非空、TopN 排名在边界上没有并列。"""
    try:
        rows = conn.execute(sql).fetchall()
    except sqlite3.Error:
        return False
    if not rows or rows == [(None,)]:
        return False
    if tpl.get("rank"):
        n = int(sql.rsplit("LIMIT", 1)[1].strip(" ;"))
        full = conn.execute(sql.rsplit("LIMIT", 1)[0]).fetchall()
        if len(full) > n and full[n - 1][-1] == full[n][-1]:
            return False  # 第 N 名和第 N+1 名并列，标准答案不唯一，丢弃
    return True


def question_space(tpl, rng, trials=2000):
    """粗略估计一个模板能生成多少种不同的问题。"""
    return len({rng.choice(tpl["qs"]).format(**tpl["params"](rng)) for _ in range(trials)})


def sample(conn, rng, templates, n, seen, cap=None):
    """随机抽样 n 条；cap[tid] 限制某个模板最多抽多少条（防止测试集把小模板的问题抽光）。"""
    out, names = [], list(templates)
    count = {t: 0 for t in names}
    tries = 0
    while len(out) < n and tries < n * 50:
        tries += 1
        tid = rng.choice(names)
        if cap and count[tid] >= cap[tid]:
            continue
        tpl = templates[tid]
        p = tpl["params"](rng)
        q = rng.choice(tpl["qs"]).format(**p)
        if q in seen:
            continue
        sql = tpl["sql"].format(**p)
        if not check(conn, tpl, sql):
            continue
        seen.add(q)
        count[tid] += 1
        out.append({
            "system": SYSTEM_PROMPT,
            "instruction": build_user_prompt(q),
            "input": "",
            "output": sql,
            # 下面两个字段只给评测脚本用，LLaMA-Factory 训练时会忽略
            "template": tid,
            "ordered": bool(tpl.get("ordered", False)),
        })
    return out


def main():
    db = HERE / "shop.db"
    build(db)
    conn = sqlite3.connect(db)
    NAMES.extend(r[0] for r in conn.execute("SELECT name FROM customers"))
    BUYER_NAMES.extend(r[0] for r in conn.execute(
        "SELECT DISTINCT c.name FROM customers c JOIN orders o ON c.id = o.customer_id"))
    PRODUCT_NAMES.extend(r[0] for r in conn.execute("SELECT name FROM products"))

    rng = random.Random(42)
    seen: set[str] = set()
    # 先抽测试集再抽训练集，并用 seen 去重：测试集里的每个问题都不会原样出现在训练集
    # 小模板（问法 × 参数组合很少）最多拿出 1/4 的问题做测试，保证训练集里还有它
    cap = {t: max(1, min(10, question_space(tpl, random.Random(0)) // 4)) for t, tpl in TEMPLATES.items()}
    test_id = sample(conn, rng, TEMPLATES, 150, seen, cap)
    test_ood = sample(conn, rng, OOD_TEMPLATES, 60, seen)
    # 训练集同样按模板封顶，避免某个参数特别多的模板（比如按客户姓名提问）独占数据
    train = sample(conn, rng, TEMPLATES, 1500, seen, {t: 80 for t in TEMPLATES})

    out_dir = HERE / "data"
    out_dir.mkdir(exist_ok=True)
    for name, rows in [("train", train), ("test_id", test_id), ("test_ood", test_ood)]:
        path = out_dir / f"text2sql_{name}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{path.name}: {len(rows)} 条")


if __name__ == "__main__":
    main()
