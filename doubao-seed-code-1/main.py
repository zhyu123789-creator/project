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

def send_to_feishu(content):
    """发送消息到飞书"""
    try:
        payload = {
            "content": f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n识别结果：{content}"
        }
        response = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        print(f"飞书发送结果: {response.status_code}")
    except Exception as e:
        print(f"飞书发送失败: {e}")

def capture_full_screen():
    """截取全屏并存储文件"""
    os.makedirs("screenshots", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = f"screenshots/full_{timestamp}.png"
    screenshot = pyautogui.screenshot()
    screenshot.save(screenshot_path)
    print(f"已保存全屏截图: {screenshot_path}")
    return screenshot_path

def main():
    print("监控启动，等待整分钟的第59秒...")
    while True:
        now = datetime.now()
        
        # 当秒数为59时执行截图和监控
        if now.second == 59:
            print(f"\n[{now.strftime('%H:%M:%S')}] 触发59秒截图和监控")
            
            # 1. 自动截图并存储文件
            capture_full_screen()
            
            # 2. 针对右侧固定区域进行监控
            right_regions = ['top_right', 'bottom_right']
            detected_chars = []
            
            for region_name in right_regions:
                region = REGIONS[region_name]
                debug_path, chars = capture_region(region, region_name, 0)
                if chars:
                    # 3. 发现空多买卖，截图通过umiocr抓取图片读取文字
                    print(f"[{region_name}] 发现目标文字: {chars}")
                    # 4. 把读取的文字通过飞书进行发送
                    # 5. 当抓取到文字立刻飞书，不要等待
                    for char in chars:
                        send_to_feishu(f"{region_name}区域: {char}")
                        detected_chars.append(char)
            
            # 3. 如果指定固定区域获取不到文字，就去左侧找已存储的文字
            if not detected_chars:
                print("右侧区域未发现目标文字，开始检查左侧区域")
                left_regions = ['top_left', 'bottom_left']
                for region_name in left_regions:
                    region = REGIONS[region_name]
                    debug_path, chars = capture_region(region, region_name, 0)
                    if chars:
                        print(f"[{region_name}] 从左侧发现目标文字: {chars}")
                        for char in chars:
                            send_to_feishu(f"{region_name}区域(左侧): {char}")
                            detected_chars.append(char)
            
            # 等待到下一分钟，避免重复执行
            while datetime.now().second == 59:
                time.sleep(0.1)
        
        # 短暂休眠，减少CPU占用
        time.sleep(0.1)

if __name__ == "__main__":
    main()