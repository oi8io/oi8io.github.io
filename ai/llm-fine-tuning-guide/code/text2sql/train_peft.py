"""不用 LLaMA-Factory，直接用 Transformers + PEFT 手写一遍 LoRA 微调。

目的不是替代框架，而是把“黑盒”拆开看清楚：
  ① 用对话模板把样本拼成一条 token 序列
  ② 构造 labels：提示词部分填 -100（不算损失），只有回答部分参与训练  ← SFT 的灵魂
  ③ 给模型挂上 LoRA，冻结原始权重，只训练 A、B 两个小矩阵
  ④ 交给 Trainer 跑“前向 → 算损失 → 反向 → 更新”的循环
  ⑤ 训练完把 LoRA 合并回原模型，导出一个普通模型

用法：
  python train_peft.py --model Qwen/Qwen2.5-Coder-1.5B-Instruct --out saves/text2sql-lora
  python train_peft.py --merge --model Qwen/Qwen2.5-Coder-1.5B-Instruct \
      --out saves/text2sql-lora --export models/qwen2.5-coder-1.5b-text2sql
"""
import argparse
import json
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, PeftModel, get_peft_model
from transformers import (AutoModelForCausalLM, AutoTokenizer, DataCollatorForSeq2Seq,
                          Trainer, TrainingArguments)

HERE = Path(__file__).parent
IGNORE = -100  # PyTorch 交叉熵会跳过标签为 -100 的位置


def encode(example, tok, max_len):
    """把一条 {system, instruction, output} 变成 input_ids / labels。"""
    prompt_msgs = [{"role": "system", "content": example["system"]},
                   {"role": "user", "content": example["instruction"]}]
    # add_generation_prompt=True 会在末尾补上 "<|im_start|>assistant\n"，也就是“该模型说话了”
    prompt = tok.apply_chat_template(prompt_msgs, tokenize=False, add_generation_prompt=True)
    answer = example["output"] + tok.eos_token  # 一定要教会模型“说完就停”
    prompt_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    answer_ids = tok(answer, add_special_tokens=False)["input_ids"]
    input_ids = (prompt_ids + answer_ids)[:max_len]
    labels = ([IGNORE] * len(prompt_ids) + answer_ids)[:max_len]
    return {"input_ids": input_ids, "attention_mask": [1] * len(input_ids), "labels": labels}


def train(args):
    tok = AutoTokenizer.from_pretrained(args.model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    use_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    model = AutoModelForCausalLM.from_pretrained(
        args.model, torch_dtype=torch.bfloat16 if use_bf16 else torch.float32)

    lora = LoraConfig(
        r=args.rank,                 # 秩：旁路矩阵的“宽度”
        lora_alpha=args.rank * 2,    # 缩放系数，实际生效的是 alpha / r
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",   # 注意力里的四个投影
                        "gate_proj", "up_proj", "down_proj"],     # MLP 里的三个投影
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()  # 亲眼看看：可训练参数只占百分之一左右

    rows = json.loads((HERE / "data/text2sql_train.json").read_text(encoding="utf-8"))
    ds = Dataset.from_list(rows).map(lambda e: encode(e, tok, args.max_len),
                                     remove_columns=list(rows[0].keys()))
    split = ds.train_test_split(test_size=0.05, seed=42)  # 留 5% 做验证集，盯着过拟合

    targs = TrainingArguments(
        output_dir=args.out,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,  # 等效 batch = batch_size × grad_accum × 卡数
        learning_rate=args.lr,
        num_train_epochs=args.epochs,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        logging_steps=10,
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        save_strategy="steps",
        save_steps=args.eval_steps,
        save_total_limit=2,
        bf16=use_bf16,
        max_steps=args.max_steps,
        report_to="none",
    )
    trainer = Trainer(
        model=model, args=targs,
        train_dataset=split["train"], eval_dataset=split["test"],
        # 负责把一批长短不一的样本补齐；labels 用 -100 补齐，补出来的位置同样不算损失
        data_collator=DataCollatorForSeq2Seq(tok, label_pad_token_id=IGNORE, padding=True),
    )
    trainer.train()
    model.save_pretrained(args.out)   # 只保存 LoRA 的 A、B 矩阵，通常只有几十 MB
    tok.save_pretrained(args.out)


def merge(args):
    """W = W0 + (alpha/r)·B·A，把旁路加回主干，得到一个不依赖 PEFT 的普通模型。"""
    tok = AutoTokenizer.from_pretrained(args.model)
    base = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16)
    merged = PeftModel.from_pretrained(base, args.out).merge_and_unload()
    merged.save_pretrained(args.export)
    tok.save_pretrained(args.export)
    print(f"合并后的模型已导出到 {args.export}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen2.5-Coder-1.5B-Instruct")
    ap.add_argument("--out", default="saves/text2sql-lora")
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--batch_size", type=int, default=8)
    ap.add_argument("--grad_accum", type=int, default=2)
    ap.add_argument("--max_len", type=int, default=1024)
    ap.add_argument("--eval_steps", type=int, default=20)
    ap.add_argument("--max_steps", type=int, default=-1, help="调试用：只跑几步就停")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--export", default="models/qwen2.5-coder-1.5b-text2sql")
    args = ap.parse_args()
    merge(args) if args.merge else train(args)
