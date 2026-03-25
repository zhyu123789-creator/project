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

# 监控区域配置（坐标已校准）
REGIONS = {
    'top_left': (823, 93, 886, 553),     # 左上象限
    'top_right': (1658, 93, 1885, 553),   # 右上象限
    'bottom_left': (823, 603, 886, 1050),  # 左下象限
    'bottom_right': (1658, 603, 1885, 1050)  # 右下象限
}

MAX_EXTEND = 100      # 最大向左扩展像素
EXTEND_STEP = 10      # 每次扩展步长
DEBUG_DIR = "debug"   # 调试图片保存目录
UMI_OCR_URL = "http://127.0.0.1:1224/api/ocr"  # UmiOCR API地址
TARGET_CHARS = {'空', '多', '买', '卖'}  # 目标识别字符
# =================================================

class MonitorConfig:
    """监控配置类"""
    def __init__(self):
        self.validate_config()
    
    def validate_config(self):
        """验证配置有效性"""
        if not FEISHU_WEBHOOK:
            raise ValueError("飞书Webhook地址未配置")
        if not REGIONS:
            raise ValueError("监控区域未配置")
        for name, region in REGIONS.items():
            if len(region) != 4:
                raise ValueError(f"区域 {name} 坐标格式错误")

class FeiShuNotifier:
    """飞书通知类"""
    @staticmethod
    def send_message(text):
        """发送文本到飞书（带错误处理）"""
        payload = {"msg_type": "text", "content": {"text": text}}
        try:
            response = requests.post(FEISHU_WEBHOOK, json=payload, timeout=2)
            response.raise_for_status()
            print(f"[飞书] {text}")
            return True
        except requests.RequestException as e:
            print(f"[飞书] 发送失败: {e}")
            return False

class ImageProcessor:
    """图像处理器类"""
    @staticmethod
    def preprocess(pil_img):
        """图像预处理：灰度、二值化、降噪，返回PIL图像"""
        img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 11, 2)
        denoised = cv2.medianBlur(binary, 3)
        return Image.fromarray(denoised)

class OCRClient:
    """OCR客户端类"""
    @staticmethod
    def recognize(image):
        """调用UmiOCR识别图像，返回识别到的文本"""
        try:
            # 直接从内存中获取base64，避免文件IO
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            img_b64 = base64.b64encode(buffer.getvalue()).decode()
            
            response = requests.post(
                UMI_OCR_URL, 
                json={"base64": img_b64, "options": {"data.format": "text"}}, 
                timeout=5
            )
            response.raise_for_status()
            return response.json().get("data", "")
        except requests.RequestException as e:
            print(f"[OCR] 请求失败: {e}")
            return ""
        except Exception as e:
            print(f"[OCR] 处理异常: {e}")
            return ""

class RegionMonitor:
    """区域监控类"""
    def __init__(self, name, region):
        self.name = name
        self.region = region
        self.left, self.top, self.right, self.bottom = region
        self.base_width = self.right - self.left
        os.makedirs(DEBUG_DIR, exist_ok=True)
    
    def capture_and_recognize(self, extend=0):
        """截图并识别"""
        current_left = self.left - extend
        screenshot = pyautogui.screenshot(region=(current_left, self.top, self.base_width, self.bottom - self.top))
        
        # 保存调试图片
        timestamp = datetime.now().strftime("%H%M%S")
        debug_path = f"{DEBUG_DIR}/{self.name}_{timestamp}_extend{extend}.png"
        screenshot.save(debug_path)
        print(f"[{self.name}] 已保存调试图片: {debug_path} (扩展{extend}px)")
        
        # 识别文本
        text = OCRClient.recognize(screenshot)
        print(f"[{self.name}] UmiOCR 识别文本: {text if text else 'No text found in image.'}")
        
        # 提取目标字
        target_chars = [ch for ch in text if ch in TARGET_CHARS]
        return debug_path, target_chars
    
    def search_char(self):
        """搜索目标字符"""
        # 原始区域 (extend=0)
        debug_path, chars = self.capture_and_recognize(0)
        if chars:
            char = chars[-1]  # 取最右侧（假设识别顺序从左到右）
            print(f"[{self.name}] 识别：{char} (扩展0px)文件: {debug_path}")
            FeiShuNotifier.send_message(f"{self.name}区域识别结果：{char}")
            return char
        else:
            print(f"[{self.name}] 识别：无 (扩展0px)")
        
        # 向左扩展搜索
        for extend in range(EXTEND_STEP, MAX_EXTEND + 1, EXTEND_STEP):
            debug_path, chars = self.capture_and_recognize(extend)
            if chars:
                char = chars[-1]
                print(f"[{self.name}] 识别：{char} (扩展{extend}px)文件: {debug_path}")
                FeiShuNotifier.send_message(f"{self.name}区域识别结果：{char}")
                return char
            else:
                print(f"[{self.name}] 识别：无 (扩展{extend}px)")
            time.sleep(0.2)
        
        return None

def worker(name, region, results):
    """线程任务：识别一个区域，将结果存入results字典"""
    monitor = RegionMonitor(name, region)
    char = monitor.search_char()
    results[name] = char if char else '？'

def main():
    """主函数"""
    # 验证配置
    try:
        MonitorConfig()
    except ValueError as e:
        print(f"配置错误: {e}")
        return
    
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
        
        # 创建并启动线程
        for name, region in REGIONS.items():
            t = threading.Thread(target=worker, args=(name, region, results))
            t.daemon = True  # 设置为守护线程
            t.start()
            threads.append(t)
        
        # 等待所有线程完成
        for t in threads:
            t.join(timeout=30)  # 添加超时控制
        
        # 打印识别结果
        print("识别结果:")
        for name, result in results.items():
            print(f"  {name}: {result}")

if __name__ == "__main__":
    main()