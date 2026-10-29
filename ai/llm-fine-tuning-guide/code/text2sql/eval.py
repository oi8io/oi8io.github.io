"""用“执行准确率”（Execution Accuracy, EX）评测模型。

对每道题：模型生成 SQL → 在 shop.db 上执行 → 和标准 SQL 的执行结果比对。
结果一样就算对，不要求 SQL 字面一致（同一个问题可以有很多种正确写法）。

用法：
  # 1) 基座模型，原样提问
  python eval.py --model Qwen/Qwen2.5-Coder-1.5B-Instruct
  # 2) 基座模型 + 在提示词里写明业务口径（提示工程基线）
  python eval.py --model Qwen/Qwen2.5-Coder-1.5B-Instruct --rules
  # 3) 基座模型 + LoRA 适配器（不合并，直接挂载）
  python eval.py --model Qwen/Qwen2.5-Coder-1.5B-Instruct --adapter saves/text2sql-lora
  # 4) 合并导出后的完整模型
  python eval.py --model models/qwen2.5-coder-1.5b-text2sql
"""
import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from common import BUSINESS_RULES, build_messages, extract_sql, run_sql, same_result

HERE = Path(__file__).parent


def load(model_path, adapter=None):
    tok = AutoTokenizer.from_pretrained(model_path)
    tok.padding_side = "left"  # 批量生成时要左填充，让每条样本的“最后一个 token”对齐
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    model = AutoModelForCausalLM.from_pretrained(model_path, torch_dtype=dtype)
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter)
    model.to("cuda" if torch.cuda.is_available() else "cpu").eval()
    return tok, model


@torch.no_grad()
def generate(tok, model, questions, rules=False, batch_size=16, max_new_tokens=256):
    outputs = []
    for i in range(0, len(questions), batch_size):
        chats = []
        for q in questions[i:i + batch_size]:
            msgs = build_messages(q)
            if rules:
                msgs[0]["content"] += "\n\n" + BUSINESS_RULES
            chats.append(tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True))
        enc = tok(chats, return_tensors="pt", padding=True).to(model.device)
        gen = model.generate(**enc, max_new_tokens=max_new_tokens, do_sample=False,
                             pad_token_id=tok.pad_token_id or tok.eos_token_id)
        for row in gen[:, enc["input_ids"].shape[1]:]:
            outputs.append(tok.decode(row, skip_special_tokens=True))
    return outputs


def question_of(item):
    # instruction 形如 “数据库表结构如下：...\n\n问题：XXX\n\n只输出一条 SQL...”
    return item["instruction"].split("问题：", 1)[1].rsplit("\n\n只输出", 1)[0]


def evaluate(tok, model, path, rules, limit=None):
    data = json.loads(Path(path).read_text(encoding="utf-8"))[:limit]
    conn = sqlite3.connect(HERE / "shop.db")
    raw = generate(tok, model, [question_of(x) for x in data], rules=rules)
    per_tpl, wrong = defaultdict(lambda: [0, 0]), []
    for item, text in zip(data, raw):
        sql = extract_sql(text)
        gold = run_sql(conn, item["output"])
        try:
            ok = same_result(run_sql(conn, sql), gold, item["ordered"])
            err = None
        except Exception as e:  # SQL 语法错误、列名不存在等
            ok, err = False, str(e)
        per_tpl[item["template"]][0] += ok
        per_tpl[item["template"]][1] += 1
        if not ok:
            wrong.append({"question": question_of(item), "gold": item["output"], "pred": sql, "error": err})
    total = sum(v[0] for v in per_tpl.values()), sum(v[1] for v in per_tpl.values())
    return total, per_tpl, wrong


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter")
    ap.add_argument("--rules", action="store_true", help="在系统提示词里附上业务口径（提示工程基线）")
    ap.add_argument("--limit", type=int, help="只评测前 N 条，调试用")
    args = ap.parse_args()

    tok, model = load(args.model, args.adapter)
    for split in ["test_id", "test_ood"]:
        (right, n), per_tpl, wrong = evaluate(tok, model, HERE / f"data/text2sql_{split}.json", args.rules, args.limit)
        print(f"\n=== {split}: EX = {right}/{n} = {right / n:.1%}")
        for tid, (r, c) in sorted(per_tpl.items(), key=lambda kv: kv[1][0] / kv[1][1]):
            print(f"  {tid:<24} {r}/{c}")
        out = HERE / f"eval_wrong_{split}.json"
        out.write_text(json.dumps(wrong, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  错题已写入 {out.name}")


if __name__ == "__main__":
    main()
