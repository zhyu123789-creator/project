import base64
import time
import os
import io
import threading
import requests
from datetime import datetime
from pathlib import Path

import pyautogui
import cv2
import numpy as np
from PIL import Image

# ==================== 配置区域 ====================
# 飞书机器人 Webhook 地址
FEISHU_WEBHOOK = 'https://www.feishu.cn/flow/api/trigger-webhook/7f6689231cc6c7e14b1685038eb13b8c'

# 右侧监控区域（优先监控区域）
RIGHT_REGIONS = {
    'top_right': (1658, 93, 1885, 553),   # 右上
    'bottom_right': (1658, 603, 1885, 1050) # 右下
}

# 左侧备份区域（右侧识别失败时使用）
LEFT_REGIONS = {
    'top_left': (823, 93, 886, 553),     # 左上象限
    'bottom_left': (823, 603, 886, 1050), # 左下象限
}

# 截图存储目录
SCREENSHOT_DIR = "screenshots"
DEBUG_DIR = "debug"

# OCR 服务地址
UMI_URL = "http://127.0.0.1:1224/api/ocr"

# 监控间隔（秒）
MONITOR_INTERVAL = 1

# 自动截图时间（59 秒）
AUTO_CAPTURE_SECOND = 59

MAX_EXTEND = 100      # 最大向左扩展像素
EXTEND_STEP = 10      # 每次扩展步长
# =================================================




def send_to_feishu(text):
    """
    通过飞书 Webhook 发送消息
    消息格式：包含发送时间和识别的文字
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "msg_type": "text",
        "content": {
            "text": f"【识别时间】{timestamp}\n【识别文字】{text}"
        }
    }
    
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=5)
        if resp.status_code == 200:
            print(f"[飞书] 消息发送成功：{text}")
        else:
            print(f"[飞书] 发送失败：{resp.status_code}")
    except Exception as e:
        print(f"[飞书] 发送异常：{e}")

def capture_region(region, name, extend=0, save_screenshot=False):
    """
    截取区域，调用 UmiOCR 识别，返回识别到的文字列表（仅包含目标字）
    同时打印详细日志，包括扩展步长和调试图片文件名
    
    Args:
        region: 截图区域 (left, top, right, bottom)
        name: 区域名称
        extend: 向左扩展像素
        save_screenshot: 是否保存截图到 screenshots 目录
    
    Returns:
        (debug_path, target_chars): 调试图片路径和识别到的目标字符列表
    """
    left, top, right, bottom = region
    current_left = left - extend
    width = right - current_left
    height = bottom - top
    
    screenshot = pyautogui.screenshot(region=(current_left, top, width, height))
    
    # 保存调试图片
    os.makedirs(DEBUG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%H%M%S")
    debug_path = f"{DEBUG_DIR}/{name}_{timestamp}_extend{extend}.png"
    screenshot.save(debug_path)
    print(f"[{name}] 已保存调试图片：{debug_path} (扩展{extend}px)")
    
    # 如果需要保存截图到 screenshots 目录
    if save_screenshot:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        screenshot_path = f"{SCREENSHOT_DIR}/{name}_{timestamp}.png"
        screenshot.save(screenshot_path)
        print(f"[{name}] 已保存截图：{screenshot_path}")
    
    # 将截图转为 base64 发送给 UmiOCR
    with open(debug_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    
    try:
        resp = requests.post(UMI_URL, json={"base64": img_b64, "options": {"data.format": "text"}}, timeout=5)
        if resp.status_code == 200:
            text = resp.json().get("data", "")
            print(f"[{name}] UmiOCR 识别文本：{text if text else 'No text found in image.'}")
            # 提取目标字（空多买卖），按出现顺序返回
            target_chars = [ch for ch in text if ch in ('空', '多', '买', '卖')]
            return debug_path, target_chars
        else:
            print(f"[{name}] UmiOCR 请求失败：{resp.status_code}")
            return debug_path, []
    except Exception as e:
        print(f"[{name}] UmiOCR 调用异常：{e}")
        return debug_path, []

def search_char_in_region(region, name, send_immediately=True):
    """
    先尝试原始区域，若找到字则立即返回最右侧的一个；
    若未找到则向左扩展搜索，直到找到或达到最大扩展。
    识别到字后立即发送飞书。
    
    Args:
        region: 搜索区域
        name: 区域名称
        send_immediately: 是否立即发送飞书消息
    
    Returns:
        识别到的字符，未找到返回 None
    """
    left, top, right, bottom = region
    base_width = right - left
    
    # 原始区域 (extend=0)
    debug_path, chars = capture_region(region, name, 0)
    if chars:
        char = chars[-1]  # 取最右侧（假设识别顺序从左到右）
        print(f"[{name}] 识别：{char} (扩展 0px) 文件：{debug_path}")
        if send_immediately:
            send_to_feishu(f"{name}区域识别结果：{char}")
        return char
    else:
        print(f"[{name}] 识别：无 (扩展 0px)")
    
    # 向左扩展搜索
    for extend in range(EXTEND_STEP, MAX_EXTEND + 1, EXTEND_STEP):
        current_left = left - extend
        debug_path, chars = capture_region(
            (current_left, top, current_left + base_width, bottom),
            name, extend
        )
        if chars:
            char = chars[-1]
            print(f"[{name}] 识别：{char} (扩展{extend}px) 文件：{debug_path}")
            if send_immediately:
                send_to_feishu(f"{name}区域识别结果：{char}")
            return char
        else:
            print(f"[{name}] 识别：无 (扩展{extend}px)")
        time.sleep(0.2)
    
    return None

def search_in_saved_screenshots():
    """
    在已存储的截图中查找文字
    遍历 screenshots 目录下的所有图片，使用 UmiOCR 识别
    """
    if not os.path.exists(SCREENSHOT_DIR):
        print("[备份] 截图目录不存在")
        return None
    
    screenshot_files = [f for f in os.listdir(SCREENSHOT_DIR) if f.endswith('.png')]
    if not screenshot_files:
        print("[备份] 没有已存储的截图")
        return None
    
    # 按时间排序，最新的在前
    screenshot_files.sort(reverse=True)
    
    for filename in screenshot_files:
        screenshot_path = os.path.join(SCREENSHOT_DIR, filename)
        try:
            with open(screenshot_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            
            resp = requests.post(UMI_URL, json={"base64": img_b64, "options": {"data.format": "text"}}, timeout=5)
            if resp.status_code == 200:
                text = resp.json().get("data", "")
                target_chars = [ch for ch in text if ch in ('空', '多', '买', '卖')]
                if target_chars:
                    char = target_chars[-1]
                    print(f"[备份] 从 {filename} 识别到：{char}")
                    return char
        except Exception as e:
            print(f"[备份] 识别 {filename} 失败：{e}")
    
    print("[备份] 未在已存储截图中找到目标文字")
    return None

def monitor_right_regions():
    """
    监控右侧固定区域，如果发现空多买卖，截图并通过 UmiOCR 识别
    识别到文字后立即发送飞书，不等待
    """
    for name, region in RIGHT_REGIONS.items():
        char = search_char_in_region(region, name, send_immediately=True)
        if char:
            # 识别到文字，立即发送并返回
            return char
    
    # 右侧区域未识别到文字，尝试从已存储的截图中查找
    char = search_in_saved_screenshots()
    if char:
        send_to_feishu(f"从备份截图识别到：{char}")
        return char
    
    return None

def auto_capture_at_59s():
    """
    在第 59 秒时自动截图并存储文件
    """
    while True:
        now = datetime.now()
        if now.second == AUTO_CAPTURE_SECOND:
            print(f"[自动截图] 开始截取所有区域...")
            
            # 截取所有区域并保存
            for name, region in {**LEFT_REGIONS, **RIGHT_REGIONS}.items():
                capture_region(region, name, save_screenshot=True)
            
            print(f"[自动截图] 完成")
            # 等待 1 秒，避免重复触发
            time.sleep(1)
        
        time.sleep(0.5)  # 每 0.5 秒检查一次

def worker(name, region, results):
    """线程任务：识别一个区域，将结果存入 results 字典"""
    char = search_char_in_region(region, name, send_immediately=True)
    results[name] = char if char else '？'

def main():
    # 创建目录
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    os.makedirs(DEBUG_DIR, exist_ok=True)
    
    # 启动自动截图线程（59 秒自动截图）
    auto_capture_thread = threading.Thread(target=auto_capture_at_59s, daemon=True)
    auto_capture_thread.start()
    print("[系统] 自动截图线程已启动")
    
    # 主监控循环
    print("[系统] 开始监控右侧区域...")
    while True:
        try:
            # 监控右侧区域
            monitor_right_regions()
            
            # 等待下一次监控
            time.sleep(MONITOR_INTERVAL)
        except KeyboardInterrupt:
            print("[系统] 程序终止")
            break
        except Exception as e:
            print(f"[系统] 监控异常：{e}")
            time.sleep(1)



if __name__ == "__main__":
    main()