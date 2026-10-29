"""把微调好的模型包装成 HTTP 服务。

提供两个接口：
  POST /v1/chat/completions   OpenAI 兼容格式，任何 OpenAI SDK / 后端框架都能直接调用
  POST /text2sql              业务接口：传入问题 → 生成 SQL → 只读校验 → 执行并返回结果

启动：
  MODEL_PATH=models/qwen2.5-coder-1.5b-text2sql uvicorn serve:app --host 0.0.0.0 --port 8000
"""
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

from common import build_messages, extract_sql, is_read_only

HERE = Path(__file__).parent
MODEL_PATH = os.environ.get("MODEL_PATH", "models/qwen2.5-coder-1.5b-text2sql")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH, torch_dtype=torch.bfloat16 if DEVICE == "cuda" else torch.float32).to(DEVICE).eval()
lock = threading.Lock()  # 单卡单模型，一次只跑一个生成请求；生产环境请换 vLLM 等推理引擎

app = FastAPI(title="Text-to-SQL 微调模型服务")


def chat(messages: list[dict], max_new_tokens: int = 256) -> str:
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt").to(DEVICE)
    with lock, torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                             pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id)
    return tokenizer.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)


class ChatRequest(BaseModel):
    model: str = "text2sql"
    messages: list[dict]
    max_tokens: int = 256


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    text = chat(req.messages, req.max_tokens)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
    }


class SQLRequest(BaseModel):
    question: str
    execute: bool = True


@app.post("/text2sql")
def text2sql(req: SQLRequest):
    sql = extract_sql(chat(build_messages(req.question)))
    if not is_read_only(sql):
        # 模型再可靠也只是概率机器：凡是会改数据的语句，一律在服务端拦下
        raise HTTPException(status_code=400, detail=f"拒绝执行非只读语句：{sql}")
    result = {"question": req.question, "sql": sql}
    if req.execute:
        # 以只读模式打开数据库，作为第二道保险
        conn = sqlite3.connect(f"file:{HERE / 'shop.db'}?mode=ro", uri=True)
        try:
            cur = conn.execute(sql)
            result["columns"] = [d[0] for d in cur.description]
            result["rows"] = cur.fetchmany(100)
        except sqlite3.Error as e:
            result["error"] = str(e)
        finally:
            conn.close()
    return result


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_PATH, "device": DEVICE}
