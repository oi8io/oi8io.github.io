"""选读：在 SFT 之后，用 GRPO + 可验证奖励（执行结果是否正确）继续训练。

奖励函数完全由程序计算：SQL 能执行、且结果和标准答案一致得 1 分，否则 0 分。
不需要人写标准 SQL 给模型抄，也不需要单独训练奖励模型。

用法：
  python train_grpo.py --model models/qwen2.5-coder-1.5b-text2sql --out saves/text2sql-grpo
"""
import argparse
import json
import sqlite3
from pathlib import Path

import torch
from datasets import Dataset
from trl import GRPOConfig, GRPOTrainer

from common import build_messages, extract_sql, run_sql, same_result

HERE = Path(__file__).parent
conn = sqlite3.connect(HERE / "shop.db", check_same_thread=False)


def execution_reward(completions, gold_sql, ordered, **kwargs):
    """GRPO 的奖励函数。completions 是一组模型回答；gold_sql、ordered 来自数据集里的同名列。"""
    rewards = []
    for completion, gold, ord_ in zip(completions, gold_sql, ordered):
        text = completion[0]["content"] if isinstance(completion, list) else completion
        sql = extract_sql(text)
        try:
            ok = same_result(run_sql(conn, sql), run_sql(conn, gold), ord_)
        except Exception:  # 语法错误、字段不存在……一律 0 分
            ok = False
        rewards.append(1.0 if ok else 0.0)
    return rewards


def load_dataset():
    rows = json.loads((HERE / "data/text2sql_train.json").read_text(encoding="utf-8"))
    items = []
    for r in rows:
        q = r["instruction"].split("问题：", 1)[1].rsplit("\n\n只输出", 1)[0]
        items.append({"prompt": build_messages(q), "gold_sql": r["output"], "ordered": r["ordered"]})
    return Dataset.from_list(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/qwen2.5-coder-1.5b-text2sql")
    ap.add_argument("--out", default="saves/text2sql-grpo")
    ap.add_argument("--max_steps", type=int, default=-1, help="调试用：只跑几步")
    args = ap.parse_args()

    use_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    cfg = GRPOConfig(
        output_dir=args.out,
        num_generations=8,              # 每道题采样 8 条 SQL，组内比较
        per_device_train_batch_size=8,  # 需要能被 num_generations 整除
        gradient_accumulation_steps=2,
        learning_rate=1e-5,             # 强化学习的学习率要比 SFT 小得多
        beta=0.04,                      # KL 惩罚系数：别离参考模型太远
        max_completion_length=256,
        temperature=1.0,                # 采样要有随机性，组内才会有好有坏
        logging_steps=5,
        bf16=use_bf16,
        max_steps=args.max_steps,
        report_to="none",
    )
    trainer = GRPOTrainer(
        model=args.model,
        reward_funcs=execution_reward,
        args=cfg,
        train_dataset=load_dataset(),
    )
    trainer.train()
    trainer.save_model(args.out)


if __name__ == "__main__":
    main()
