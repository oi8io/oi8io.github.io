"""训练、评测、部署共用的提示词与 SQL 比对工具。

关键原则：训练时模型看到的提示词，必须和部署时一模一样。
所以“怎么拼提示词”只在这一个文件里定义，其他脚本都从这里导入。
"""
import re
import sqlite3
from itertools import permutations

from build_db import SCHEMA

SYSTEM_PROMPT = "你是一名资深数据分析师，负责把业务人员的问题翻译成可以直接执行的 SQLite 查询。"


def build_user_prompt(question: str, schema: str = SCHEMA) -> str:
    return f"数据库表结构如下：\n{schema}\n\n问题：{question}\n\n只输出一条 SQL，不要解释。"


def build_messages(question: str) -> list[dict]:
    """OpenAI / ChatML 风格的对话消息，交给 tokenizer.apply_chat_template 渲染。"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(question)},
    ]


# “业务口径”写成文字版，只在评测“基座模型 + 长提示词”这条基线时使用。
# 微调后的模型不需要它——口径已经写进了参数里。
BUSINESS_RULES = """业务口径（必须遵守）：
1. “有效订单”指 status 为 paid、shipped、completed 的订单；除非问题明确提到取消或退款，订单数、销售额都只统计有效订单。
2. “销售额 / 营收 / GMV”= SUM(order_items.quantity * order_items.unit_price_cent) / 100.0，单位为元，用 ROUND(..., 2) 保留两位小数。
3. 金额字段都以“分”存储，输出给业务人员时要换算成元。
4. 日期按 SQLite 写法处理，例如按月过滤用 strftime('%Y-%m', order_date) = '2024-03'。
5. 涉及商品排行时输出商品名称 products.name。"""


def extract_sql(text: str) -> str:
    """从模型输出里抠出 SQL：去掉 ```sql 围栏、<think> 段落和多余解释。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    fence = re.search(r"```(?:sql|sqlite)?\s*(.*?)```", text, flags=re.S | re.I)
    if fence:
        text = fence.group(1)
    text = text.strip()
    # 只保留第一条语句
    if ";" in text:
        text = text.split(";")[0] + ";"
    return text.strip()


def run_sql(conn: sqlite3.Connection, sql: str):
    cur = conn.execute(sql)
    return cur.fetchall()


def _normalize(rows):
    norm = []
    for row in rows:
        norm.append(tuple(round(v, 2) if isinstance(v, float) else v for v in row))
    return norm


def same_result(pred_rows, gold_rows, ordered: bool) -> bool:
    """执行结果比对（Execution Accuracy）。

    - 只比数值，不比列名；
    - 标准答案没有要求顺序时，忽略行顺序；
    - 模型多输出了几列（比如排名题顺手带上了金额）不算错，只要标准答案的每一列都能在里面找到。
    """
    p, g = _normalize(pred_rows), _normalize(gold_rows)
    if len(p) != len(g):
        return False
    if not g:
        return True

    def covers(big, small, must_keep_first=False):
        # 能否从 big 里挑出若干列（任意顺序），恰好等于 small
        width = len(small[0])
        if len(big[0]) < width:
            return False
        for cols in permutations(range(len(big[0])), width):
            if must_keep_first and 0 not in cols:
                continue
            proj = [tuple(row[i] for i in cols) for row in big]
            if ordered and proj == small:
                return True
            if not ordered and sorted(map(repr, proj)) == sorted(map(repr, small)):
                return True
        return False

    # 模型多输出几列：没问题；模型少输出几列（比如排名题只给了商品名、没给销量）：
    # 只要它给出的列都对、并且包含标准答案的第一列（“是谁 / 是哪个”），也算对
    return covers(p, g) or covers(g, p, must_keep_first=True)


def is_read_only(sql: str) -> bool:
    """部署时的安全闸门：只允许 SELECT / WITH 开头的只读查询。"""
    head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
    return head in {"SELECT", "WITH"}
