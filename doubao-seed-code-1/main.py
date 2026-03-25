import base64
import time
import os
import io
import threading
import requests
from datetime import datetime

import pyautogui
import cv2
import numpy as np
from PIL import Image

# ==================== 配置区域 ====================
# 飞书机器人 Webhook 地址（请替换为你自己的）

FEISHU_WEBHOOK = 'https://www.feishu.cn/flow/api/trigger-webhook/7f6689231cc6c7e14b1685038eb13b8c'

# 只保留左上和左下两个区域（坐标已校准）
REGIONS = {
    'top_left': (823, 93, 886, 553),     # 左上象限
'top_right':    (1658, 93, 1885, 553),  # 右上
    'bottom_left': (823, 603, 886, 1050), # 左下象限
'bottom_right': (1658, 603, 1885, 1050) # 右下
}

MAX_EXTEND = 100      # 最大向左扩展像素
EXTEND_STEP = 10      # 每次扩展步长
# =================================================

def send_to_feishu(text):
    """发送文本到飞书（带重试）"""
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        requests.post(FEISHU_WEBHOOK, json=payload, timeout=2)
        print(f"[飞书] {text}")
    except Exception as e:
        print(f"[飞书] 发送失败: {e}")

def preprocess_image(pil_img):
    """图像预处理：灰度、二值化、降噪，返回PIL图像"""
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 11, 2)
    denoised = cv2.medianBlur(binary, 3)
    return Image.fromarray(denoised)

def capture_region(region, name, extend=0):
    """
    截取区域，调用UmiOCR识别，返回识别到的文字列表（仅包含目标字）
    同时打印详细日志，包括扩展步长和调试图片文件名
    """
    left, top, right, bottom = region
    current_left = left - extend
    screenshot = pyautogui.screenshot(region=(current_left, top, right-left, bottom-top))

    os.makedirs("debug", exist_ok=True)
    timestamp = datetime.now().strftime("%H%M%S")
    debug_path = f"debug/{name}_{timestamp}_extend{extend}.png"
    screenshot.save(debug_path)
    print(f"[{name}] 已保存调试图片: {debug_path} (扩展{extend}px)")

    # 将截图转为 base64 发送给 UmiOCR
    with open(debug_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    umi_url = "http://127.0.0.1:1224/api/ocr"
    try:
        resp = requests.post(umi_url, json={"base64": img_b64, "options": {"data.format": "text"}}, timeout=5)
        if resp.status_code == 200:
            text = resp.json().get("data", "")
            print(f"[{name}] UmiOCR 识别文本: {text if text else 'No text found in image.'}")
            # 提取目标字（空多买卖），按出现顺序返回
            target_chars = [ch for ch in text if ch in ('空', '多', '买', '卖')]
            return debug_path, target_chars  # 返回文件名和识别结果
        else:
            print(f"[{name}] UmiOCR 请求失败: {resp.status_code}")
            return debug_path, []
    except Exception as e:
        print(f"[{name}] UmiOCR 调用异常: {e}")
        return debug_path, []

def search_char_in_region(region, name):
    """
    先尝试原始区域，若找到字则立即返回最右侧的一个；
    若未找到则向左扩展搜索，直到找到或达到最大扩展。
    识别到字后立即发送飞书。
    """
    left, top, right, bottom = region
    base_width = right - left

    # 原始区域 (extend=0)
    debug_path,chars = capture_region(region, name, 0)
    if chars:
        char = chars[-1]  # 取最右侧（假设识别顺序从左到右）
        print(f"[{name}] 识别：{char} (扩展0px)文件: {debug_path}")
        send_to_feishu(f"{name}区域识别结果：{char}")
        return char
    else:
        print(f"[{name}] 识别：无 (扩展0px)")

    # 向左扩展搜索
    for extend in range(EXTEND_STEP, MAX_EXTEND + 1, EXTEND_STEP):
        current_left = left - extend
        debug_path,chars = capture_region(
            (current_left, top, current_left + base_width, bottom),
            name, extend
        )
        if chars:
            char = chars[-1]
            print(f"[{name}] 识别：{char} (扩展{extend}px)文件: {debug_path}")
            send_to_feishu(f"{name}区域识别结果：{char}")
            return char
        else:
            print(f"[{name}] 识别：无 (扩展{extend}px)")
        time.sleep(0.2)

    return None

def worker(name, region, results):
    """线程任务：识别一个区域，将结果存入results字典"""
    debug_path,char = search_char_in_region(region, name)
    results[name] = char if char else '？'

def main():
    print("监控启动，等待整分钟的第59秒...")
    while True:
        now = datetime.now()
        if now.second != 59:
            if now.second < 59:
                wait_sec = 59 - now.second
            else:
                wait_sec = 60 - (now.second - 59)
            time.sleep(wait_sec - now.microsecond / 1_000_000)

        start_time = datetime.now()
        print(f"\n[{start_time}] 开始识别")

        results = {}
        threads = []
        for name, region in REGIONS.items():
            t = threading.Thread(target=worker, args=(name, region, results))
            t.start()
            threads.append(t)

        # 等待所有线程完成
        for t in threads:
            t.join()



if __name__ == "__main__":
    main()