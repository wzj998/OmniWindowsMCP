#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 OmniWindowsMCP - 测试5种Windows操作
"""

import sys
import json
import os
import requests
import time
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# MCP 服务器配置
# 在本地运行时，使用 localhost
MCP_HOST = os.environ.get("MCP_HOST", "localhost")
MCP_PORT = int(os.environ.get("MCP_PORT", "20004"))
MCP_ENDPOINT = f"http://{MCP_HOST}:{MCP_PORT}/mcp"

def call_tool(tool_name: str, arguments: dict = None):
    """
    调用MCP工具
    
    Args:
        tool_name: 工具名称
        arguments: 工具参数
        
    Returns:
        tuple: (success: bool, message: str, result: dict)
    """
    try:
        if arguments is None:
            arguments = {}
        
        # 构建 MCP JSON-RPC 请求
        request_data = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        
        print(f"\n调用工具: {tool_name}")
        if arguments:
            print(f"参数: {json.dumps(arguments, ensure_ascii=False, indent=2)}")
        print("-" * 80)
        
        # 发送请求到 MCP 服务器
        response = requests.post(
            MCP_ENDPOINT,
            json=request_data,
            timeout=30
        )
        
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}", {}
        
        result = response.json()
        
        # 检查是否有错误
        if "error" in result:
            error = result["error"]
            return False, error.get('message', '未知错误'), result
        
        # 提取结果
        if "result" in result:
            result_data = result["result"]
            
            # 检查是否有结构化内容
            if "structuredContent" in result_data:
                structured = result_data["structuredContent"]
                success = structured.get("success", False)
                message = structured.get("message", "")
                return success, message, structured
            
            # 检查是否有文本内容
            if "content" in result_data:
                messages = []
                for content_item in result_data["content"]:
                    if content_item.get("type") == "text":
                        messages.append(content_item.get("text", ""))
                return True, "\n".join(messages), result_data
        
        return True, "操作成功", result
        
    except requests.exceptions.ConnectionError:
        return False, f"无法连接到 MCP 服务器 ({MCP_ENDPOINT})，请确保 OmniWindowsMCP 服务器正在运行", {}
    except requests.exceptions.Timeout:
        return False, "请求超时", {}
    except Exception as e:
        return False, f"错误: {str(e)}", {}

def test_screenshot():
    """测试截屏功能"""
    print("\n" + "=" * 80)
    print("测试 1: 截屏")
    print("=" * 80)
    
    success, message, result = call_tool("screenshot")
    
    print(f"结果: success={success}")
    print(f"消息: {message}")
    
    if success and "image_base64" in result:
        image_base64 = result["image_base64"]
        print(f"截图base64长度: {len(image_base64)} 字符")
        print("提示: 截图已成功获取，base64数据已保存在结果中")
    
    return success, message, result

def test_left_click(x: int, y: int):
    """测试左键点击"""
    print("\n" + "=" * 80)
    print("测试 2: 左键点击")
    print("=" * 80)
    
    success, message, result = call_tool("left_click", {"x": x, "y": y})
    
    print(f"结果: success={success}")
    print(f"消息: {message}")
    if "x" in result and "y" in result:
        print(f"点击坐标: ({result['x']}, {result['y']})")
    
    return success, message, result

def test_right_click(x: int, y: int):
    """测试右键点击"""
    print("\n" + "=" * 80)
    print("测试 3: 右键点击")
    print("=" * 80)
    
    success, message, result = call_tool("right_click", {"x": x, "y": y})
    
    print(f"结果: success={success}")
    print(f"消息: {message}")
    if "x" in result and "y" in result:
        print(f"点击坐标: ({result['x']}, {result['y']})")
    
    return success, message, result

def test_double_click(x: int, y: int):
    """测试双击"""
    print("\n" + "=" * 80)
    print("测试 4: 双击")
    print("=" * 80)
    
    success, message, result = call_tool("double_click", {"x": x, "y": y})
    
    print(f"结果: success={success}")
    print(f"消息: {message}")
    if "x" in result and "y" in result:
        print(f"双击坐标: ({result['x']}, {result['y']})")
    
    return success, message, result

def test_input_text(text: str):
    """测试输入文本"""
    print("\n" + "=" * 80)
    print("测试 5: 输入文本")
    print("=" * 80)
    
    success, message, result = call_tool("input_text", {"text": text})
    
    print(f"结果: success={success}")
    print(f"消息: {message}")
    if "text" in result:
        text_preview = result["text"][:50] + "..." if len(result["text"]) > 50 else result["text"]
        print(f"输入的文本: {text_preview}")
    
    return success, message, result

def main():
    seconds_wait = 1

    """主函数"""
    print("=" * 80)
    print("测试 OmniWindowsMCP - 5种Windows操作")
    print("=" * 80)
    print(f"MCP服务器地址: {MCP_ENDPOINT}")
    print("\n提示: 请确保 OmniWindowsMCP 服务器正在运行")
    print("提示: 测试鼠标操作时，请确保鼠标不会干扰测试")
    print("提示: 测试输入文本时，请先打开一个文本编辑器或输入框")
    print()
    
    # 测试1: 截屏
    test_screenshot()
    
    # 等待一下，让用户看到结果
    print(f"\n等待{seconds_wait}秒后继续测试鼠标操作...")
    time.sleep(seconds_wait)
    
    # 测试2: 左键点击（屏幕中心）
    import pyautogui
    screen_width, screen_height = pyautogui.size()
    center_x = screen_width // 2
    center_y = screen_height // 2
    
    print(f"\n屏幕尺寸: {screen_width} x {screen_height}")
    print(f"将在屏幕中心 ({center_x}, {center_y}) 进行鼠标操作测试")
    print(f"\n等待{seconds_wait}秒，请将鼠标移开...")
    time.sleep(seconds_wait)
    
    test_left_click(center_x, center_y)
    
    time.sleep(seconds_wait)
    
    # 测试3: 右键点击
    test_right_click(center_x, center_y)
    
    time.sleep(seconds_wait)
    
    # 测试4: 双击
    test_double_click(center_x, center_y)
    
    # 测试5: 输入文本
    print("\n等待1秒后测试文本输入...")
    print("提示: 请确保当前焦点在一个可以输入文本的地方（如记事本、输入框等）")
    time.sleep(seconds_wait)
    
    test_input_text("Hello, OmniWindowsMCP! 这是测试文本。")
    
    print("\n" + "=" * 80)
    print("所有测试完成！")
    print("=" * 80)

if __name__ == "__main__":
    main()
