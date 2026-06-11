import os
import re
import tempfile
from telegram.ext import ContextTypes
from openai import AsyncOpenAI

async def optimize_python_code(chat_id: int, user_instruction: str, code_content: str, original_filename: str, context: ContextTypes.DEFAULT_TYPE, status_msg):
    """
    員工 2：資深 Python 架構師與效能優化專家。
    負責接收龐大程式碼，進行深度重構與優化，保證程式碼完整不截斷。
    """
    nvidia_api_key = os.environ.get("NVIDIA_API_KEY")
    if not nvidia_api_key:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ 系統錯誤：缺少 NVIDIA_API_KEY")
        return

    # 建立 NVIDIA OpenAI Client
    client = AsyncOpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=nvidia_api_key
    )

    await context.bot.send_message(
        chat_id=chat_id, 
        text="🛠️ [架構師]: 收到任務。我正在審視這份程式碼的 Big-O 複雜度與記憶體管理。由於檔案可能高達 100KB，這將會進行深度的 GPU/CPU 效能榨取分析，請稍候幾分鐘..."
    )

    # 系統提示詞設定 (要求絕對不截斷，並增加深度優化指標)
    system_prompt = """
    你是一位世界頂級的資深 Python 架構師與效能優化專家。
    你的任務是深度重構與優化用戶提供的 Python 程式碼。你不僅僅是修改排版，你必須找出效能瓶頸並徹底重寫不良架構。
    
    【優化執行重點】：
    1. 效能極致壓榨：優化時間複雜度與空間複雜度 (Big-O)，善用 generators、緩存 (lru_cache)、非同步或適當的資料結構 (如 set/dict 取代 list 搜尋)。
    2. 架構與防呆設計：套用 SOLID 原則，減少高耦合，模組化冗長的函數，並增加健壯的異常處理 (try-except) 與邊界條件檢查。
    3. 現代 Pythonic 規範：全面加入 PEP 8 規範、嚴謹的型別提示 (Type Hinting)、詳細的 Docstrings 以及善用 Python 標準庫。
    
    【嚴格規定 - 絕對不可違反】：
    1. 你必須回傳「完整且可以獨立運行」的 Python 程式碼，**絕對不可以**使用 "..." 或 "省略" 或 "維持原樣" 來截斷程式碼。
    2. 即使程式碼很長（高達 100KB），你也必須將完整的、優化後的所有程式碼全部寫出來。
    3. 你的回應必須分為兩部分：
       - 第一部分：用繁體中文詳細說明你做了哪些具體的「架構優化」、「效能提升(Big-O)」、「工程規範升級」。
       - 第二部分：將完整的 Python 程式碼包在 ```python 與 ``` 之間。
    """

    user_prompt = f"用戶額外指示: {user_instruction}\n\n需要優化的完整程式碼如下：\n```python\n{code_content}\n```"

    try:
        # 使用 llama-3.1-70b-instruct，支援高達 128K Token 上下文，非常適合處理 100KB 大檔案
        response = await client.chat.completions.create(
            model="meta/llama-3.1-70b-instruct",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            max_tokens=8000 # 盡可能設定最大輸出，確保程式碼不被截斷
        )
        
        reply_content = response.choices[0].message.content

        # 解析 LLM 回應，分離「說明」與「程式碼」
        explanation = reply_content
        optimized_code = ""
        
        code_match = re.search(r'```python\n(.*?)\n```', reply_content, re.DOTALL)
        if code_match:
            optimized_code = code_match.group(1)
            # 將說明文字中的程式碼區塊移除，只留下純文字說明
            explanation = reply_content[:code_match.start()].strip() + "\n\n" + reply_content[code_match.end():].strip()
        else:
            # 如果沒有找到 python 區塊，可能模型直接輸出了程式碼
            if "def " in reply_content or "import " in reply_content:
                optimized_code = reply_content
                explanation = "⚠️ 架構師直接返回了程式碼，未提供額外格式化說明。"
            else:
                await context.bot.send_message(chat_id=chat_id, text="⚠️ 架構師優化失敗，未能正確生成程式碼區塊。")
                return

        # 寫入暫存檔案以供 Telegram 回傳
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as temp_file:
            temp_file.write(optimized_code)
            temp_file_path = temp_file.name

        # 發送優化說明 (文字過長時截斷發送，Telegram 限制 4096 字元)
        safe_explanation = explanation[:4000] + ("..." if len(explanation) > 4000 else "")
        await context.bot.send_message(chat_id=chat_id, text=f"🛠️ [架構師報告]:\n{safe_explanation}")

        # 發送優化後的 Python 檔案
        new_filename = f"optimized_{original_filename}"
        with open(temp_file_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id, 
                document=f, 
                filename=new_filename,
                caption="✅ [總結]: 這是為您優化且不截斷的完整架構程式碼。"
            )
            
        # 清理暫存檔
        os.remove(temp_file_path)

    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ 處理過程中發生錯誤: {str(e)}")
