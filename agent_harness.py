"""
Agent Harness — NVIDIA API 驅動的 Python 程式優化引擎
使用 meta/llama-3.1-405b-instruct 或 nvidia/llama-3.1-nemotron-70b-instruct
"""

import os
import json
import httpx
from dataclasses import dataclass
from typing import Optional

NVIDIA_API_KEY = os.environ["NVIDIA_API_KEY"]
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL = os.getenv("NVIDIA_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct")

SYSTEM_PROMPT = """你是一位頂尖的 Python 程式碼優化專家。
你的任務是分析用戶提供的 Python 程式碼，並根據用戶的優化需求進行全面改善。

優化面向包括（依用戶指示決定優先順序）：
1. **效能** — 演算法複雜度、記憶體使用、執行速度
2. **可讀性** — PEP8 規範、命名規範、註解品質
3. **安全性** — 輸入驗證、例外處理、資源管理
4. **模組化** — 函數拆分、類別設計、職責分離
5. **測試性** — 可測試結構、依賴注入、純函數

回應格式必須是嚴格的 JSON，結構如下：
{
  "optimized_code": "<完整優化後的 Python 程式碼>",
  "summary": "<50字內的優化摘要>",
  "changes": [
    {
      "category": "<效能|可讀性|安全性|模組化|測試性>",
      "description": "<具體改動說明>",
      "impact": "<high|medium|low>"
    }
  ],
  "metrics": {
    "original_lines": <原始行數>,
    "optimized_lines": <優化後行數>,
    "estimated_improvement": "<預估改善幅度描述>"
  }
}

只回傳 JSON，不要有任何其他文字或 markdown 包裝。"""


@dataclass
class OptimizationResult:
    optimized_code: str
    summary: str
    changes: list[dict]
    metrics: dict
    raw_response: str


async def optimize_code(
    code: str,
    user_prompt: str,
    max_tokens: int = 4096,
) -> OptimizationResult:
    """
    呼叫 NVIDIA API，對 Python 程式碼進行 Agent 優化。
    支援多輪 refinement（最多 2 輪）。
    """
    user_message = f"""## 優化需求
{user_prompt}

## 原始程式碼
```python
{code}
```

請根據上述需求優化程式碼，並以 JSON 格式回傳結果。"""

    messages = [{"role": "user", "content": user_message}]
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        # === Round 1: 初次優化 ===
        response = await _call_nvidia_api(client, messages, max_tokens)
        result_text = response.strip()
        
        # 嘗試解析 JSON
        parsed = _try_parse_json(result_text)
        
        if not parsed:
            # === Round 2: 修正格式（Agent 自我修正）===
            messages.append({"role": "assistant", "content": result_text})
            messages.append({
                "role": "user",
                "content": "你的回應不是合法的 JSON。請只回傳純 JSON 物件，不要有任何其他文字、說明或 markdown。"
            })
            response2 = await _call_nvidia_api(client, messages, max_tokens)
            result_text = response2.strip()
            parsed = _try_parse_json(result_text)
        
        if not parsed:
            # Fallback: 包裝原始回應
            parsed = {
                "optimized_code": _extract_code_block(result_text) or code,
                "summary": "優化完成（格式解析失敗，已提取程式碼）",
                "changes": [{"category": "一般", "description": "請查看優化程式碼", "impact": "medium"}],
                "metrics": {
                    "original_lines": len(code.splitlines()),
                    "optimized_lines": len((_extract_code_block(result_text) or code).splitlines()),
                    "estimated_improvement": "未知"
                }
            }
        
        return OptimizationResult(
            optimized_code=parsed.get("optimized_code", code),
            summary=parsed.get("summary", ""),
            changes=parsed.get("changes", []),
            metrics=parsed.get("metrics", {}),
            raw_response=result_text,
        )


async def _call_nvidia_api(
    client: httpx.AsyncClient,
    messages: list[dict],
    max_tokens: int,
) -> str:
    """發送請求到 NVIDIA NIM API"""
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "top_p": 0.9,
    }
    
    resp = await client.post(
        f"{NVIDIA_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {NVIDIA_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _try_parse_json(text: str) -> Optional[dict]:
    """嘗試從文字中解析 JSON"""
    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 嘗試找 { ... } 區塊
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    
    return None


def _extract_code_block(text: str) -> Optional[str]:
    """從 markdown 程式碼區塊中提取程式碼"""
    import re
    pattern = r"```(?:python)?\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def format_changes_report(result: OptimizationResult) -> str:
    """將優化結果格式化為 Telegram 訊息"""
    impact_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    
    lines = [
        "✅ *優化完成*",
        "",
        f"📋 *摘要*: {result.summary}",
        "",
        "📊 *程式碼統計*",
        f"  原始行數: `{result.metrics.get('original_lines', '?')}` 行",
        f"  優化後: `{result.metrics.get('optimized_lines', '?')}` 行",
        f"  預估改善: {result.metrics.get('estimated_improvement', '?')}",
        "",
        f"🔧 *優化項目* ({len(result.changes)} 項):",
    ]
    
    for i, change in enumerate(result.changes, 1):
        emoji = impact_emoji.get(change.get("impact", "medium"), "⚪")
        lines.append(
            f"  {i}. {emoji} [{change.get('category', '?')}] {change.get('description', '')}"
        )
    
    return "\n".join(lines)
