from mcp.server import Server
import mcp.types as types
import sys
import json
import asyncio
import queue
import threading
import time
import datetime
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import copy
import pyautogui
import win32clipboard
import win32con
import win32api
import win32gui
import ctypes
from ctypes import wintypes

# 诊断：打印 pywin32 模块路径，用于判断是否导错/混装（该卸载哪个包、该装哪个 wheel）
print("win32gui.__file__ =", getattr(win32gui, "__file__", "N/A"))
print("win32api.__file__ =", getattr(win32api, "__file__", "N/A"))

from PIL import Image
import io
import base64
# path fix
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 导入字幕窗口模块
from MCPs.SubtitleWindow import subtitle_window

# ==================== 服务器配置 ====================
# 在此处配置服务器端口和主机地址
PORT = 20004  # 服务器端口号
HOST = "0.0.0.0"  # 服务器主机地址
# 字幕与操作配置
PRE_EXECUTE_DELAY_SEC = 2.0  # 执行前等待时间（秒）
SUBTITLE_HIDE_DELAY_SEC = 3.0  # 操作完成后字幕延迟隐藏时间（秒）
HOTKEY_FORCE_STOP_DELAY_SEC = 4.0  # 快捷键强制停止后响应延迟时间（秒）
GLOBAL_FORCE_STOP_HOTKEY = "win+c"  # 全局强制停止快捷键
# ===================================================

server = Server("omni-windows")
app = FastAPI(title="Omni Windows MCP Server")

FORCE_STOP_EVENT = threading.Event()
FORCE_STOP_TRIGGERED_AT = None
FORCE_STOP_LOCK = threading.Lock()

# 特殊键名 -> Windows 虚拟键码（用于多字符键如 esc/enter/tab）
SPECIAL_KEY_VK = {
    "esc": win32con.VK_ESCAPE,
    "escape": win32con.VK_ESCAPE,
    "enter": win32con.VK_RETURN,
    "return": win32con.VK_RETURN,
    "tab": win32con.VK_TAB,
    "space": win32con.VK_SPACE,
    "backspace": win32con.VK_BACK,
    "insert": win32con.VK_INSERT,
    "delete": win32con.VK_DELETE,
    "home": win32con.VK_HOME,
    "end": win32con.VK_END,
    "pageup": win32con.VK_PRIOR,
    "pagedown": win32con.VK_NEXT,
    "up": win32con.VK_UP,
    "down": win32con.VK_DOWN,
    "left": win32con.VK_LEFT,
    "right": win32con.VK_RIGHT,
    "f1": win32con.VK_F1,
    "f2": win32con.VK_F2,
    "f3": win32con.VK_F3,
    "f4": win32con.VK_F4,
    "f5": win32con.VK_F5,
    "f6": win32con.VK_F6,
    "f7": win32con.VK_F7,
    "f8": win32con.VK_F8,
    "f9": win32con.VK_F9,
    "f10": win32con.VK_F10,
    "f11": win32con.VK_F11,
    "f12": win32con.VK_F12,
}

def parse_hotkey(hotkey: str):
    """解析快捷键字符串，返回 (modifiers, vk)。modifiers 为 MOD_* 组合，用于键盘钩子中比对。"""
    parts = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
    modifiers = 0
    key_part = None
    for part in parts:
        if part in ("win", "windows", "winleft", "winright"):
            modifiers |= win32con.MOD_WIN
        elif part in ("ctrl", "control"):
            modifiers |= win32con.MOD_CONTROL
        elif part == "alt":
            modifiers |= win32con.MOD_ALT
        elif part == "shift":
            modifiers |= win32con.MOD_SHIFT
        else:
            key_part = part
    if not key_part:
        return None, None
    if len(key_part) == 1:
        vk = ord(key_part.upper())
    else:
        vk = SPECIAL_KEY_VK.get(key_part)
    return modifiers, vk


# ==================== 键盘钩子（替代 RegisterHotKey）====================
# 低级键盘钩子常量与结构（避免热键冲突、被占用等问题）
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
VK_LWIN = 0x5B
VK_RWIN = 0x5C

user32 = ctypes.windll.user32
HOOK_CB_TYPE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# 明确声明参数类型，避免 64 位下句柄被当作 c_int 导致 OverflowError
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,       # idHook
    HOOK_CB_TYPE,       # lpfn
    wintypes.HINSTANCE, # hMod (HMODULE，64 位为指针)
    wintypes.DWORD,     # dwThreadId
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_long


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


def _is_modifier_down(modifiers: int) -> bool:
    """根据当前键盘状态判断是否与目标修饰键一致（Win/Ctrl/Alt/Shift）。"""
    if modifiers & win32con.MOD_WIN:
        if not (win32api.GetAsyncKeyState(VK_LWIN) & 0x8000 or win32api.GetAsyncKeyState(VK_RWIN) & 0x8000):
            return False
    else:
        if win32api.GetAsyncKeyState(VK_LWIN) & 0x8000 or win32api.GetAsyncKeyState(VK_RWIN) & 0x8000:
            return False
    if modifiers & win32con.MOD_CONTROL:
        if not (win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000):
            return False
    else:
        if win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000:
            return False
    if modifiers & win32con.MOD_ALT:
        if not (win32api.GetAsyncKeyState(win32con.VK_MENU) & 0x8000):
            return False
    else:
        if win32api.GetAsyncKeyState(win32con.VK_MENU) & 0x8000:
            return False
    if modifiers & win32con.MOD_SHIFT:
        if not (win32api.GetAsyncKeyState(win32con.VK_SHIFT) & 0x8000):
            return False
    else:
        if win32api.GetAsyncKeyState(win32con.VK_SHIFT) & 0x8000:
            return False
    return True


# 保持回调引用，防止被 GC 回收导致钩子失效
_keyboard_hook_callback_ref = None


def start_hotkey_listener():
    """使用低级键盘钩子监听全局强制停止快捷键（替代 RegisterHotKey，避免热键占用冲突）。"""
    global _keyboard_hook_callback_ref
    modifiers, vk = parse_hotkey(GLOBAL_FORCE_STOP_HOTKEY)
    if modifiers is None or vk is None:
        print(f"[快捷键错误] 无法解析快捷键: {GLOBAL_FORCE_STOP_HOTKEY}")
        sys.stdout.flush()
        return

    def low_level_keyboard_proc(nCode, wParam, lParam):
        global FORCE_STOP_TRIGGERED_AT
        if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            hook_struct = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if hook_struct.vkCode == vk and _is_modifier_down(modifiers):
                with FORCE_STOP_LOCK:
                    FORCE_STOP_TRIGGERED_AT = time.time()
                    FORCE_STOP_EVENT.set()
                print("[快捷键] 检测到强制停止快捷键（键盘钩子），已触发停止")
                sys.stdout.flush()
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    _keyboard_hook_callback_ref = HOOK_CB_TYPE(low_level_keyboard_proc)

    def hook_thread():
        hook_id = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            _keyboard_hook_callback_ref,
            win32api.GetModuleHandle(None),
            0,
        )
        if not hook_id:
            err = win32api.GetLastError()
            print(f"[快捷键错误] 键盘钩子安装失败: {GLOBAL_FORCE_STOP_HOTKEY}，错误码: {err}")
            sys.stdout.flush()
            return
        print(f"[快捷键] 已通过键盘钩子监听全局快捷键: {GLOBAL_FORCE_STOP_HOTKEY}")
        sys.stdout.flush()
        try:
            while True:
                ret, _ = win32gui.GetMessage(None, 0, 0)
                if not ret:
                    break
        finally:
            user32.UnhookWindowsHookEx(hook_id)

    threading.Thread(target=hook_thread, daemon=True).start()

def should_force_stop():
    """检查是否触发强制停止"""
    return FORCE_STOP_EVENT.is_set()

def wait_with_force_stop(delay_seconds: float) -> bool:
    """等待指定时间，如检测到强制停止则提前返回False"""
    end_time = time.time() + delay_seconds
    while time.time() < end_time:
        if should_force_stop():
            return False
        time.sleep(0.05)
    return True

def build_force_stop_response():
    """构建快捷键强制停止响应"""
    with FORCE_STOP_LOCK:
        triggered_at = FORCE_STOP_TRIGGERED_AT or time.time()
    elapsed = time.time() - triggered_at
    remaining = max(0.0, HOTKEY_FORCE_STOP_DELAY_SEC - elapsed)
    if remaining > 0:
        time.sleep(remaining)
    FORCE_STOP_EVENT.clear()
    subtitle_window.set_completed_operation("已被快捷键强制停止")
    return [{
        "type": "object",
        "object": {
            "success": False,
            "message": "用户使用全局快捷键强制停止"
        }
    }]

# 定义工具的 outputSchema
TOOL_OUTPUT_SCHEMAS = {
    "screenshot": {
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "操作是否成功"
            },
            "message": {
                "type": "string",
                "description": "操作结果消息"
            },
            "image_base64": {
                "type": "string",
                "description": "截图的base64编码（仅在success为true时存在）"
            },
            "error": {
                "type": "string",
                "description": "错误信息（仅在success为false时存在）"
            }
        },
        "required": ["success", "message"]
    },
    "left_click": {
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "操作是否成功"
            },
            "message": {
                "type": "string",
                "description": "操作结果消息"
            },
            "x": {
                "type": "number",
                "description": "点击的X坐标"
            },
            "y": {
                "type": "number",
                "description": "点击的Y坐标"
            },
            "error": {
                "type": "string",
                "description": "错误信息（仅在success为false时存在）"
            }
        },
        "required": ["success", "message"]
    },
    "right_click": {
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "操作是否成功"
            },
            "message": {
                "type": "string",
                "description": "操作结果消息"
            },
            "x": {
                "type": "number",
                "description": "点击的X坐标"
            },
            "y": {
                "type": "number",
                "description": "点击的Y坐标"
            },
            "error": {
                "type": "string",
                "description": "错误信息（仅在success为false时存在）"
            }
        },
        "required": ["success", "message"]
    },
    "double_click": {
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "操作是否成功"
            },
            "message": {
                "type": "string",
                "description": "操作结果消息"
            },
            "x": {
                "type": "number",
                "description": "双击的X坐标"
            },
            "y": {
                "type": "number",
                "description": "双击的Y坐标"
            },
            "error": {
                "type": "string",
                "description": "错误信息（仅在success为false时存在）"
            }
        },
        "required": ["success", "message"]
    },
    "input_text": {
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "操作是否成功"
            },
            "message": {
                "type": "string",
                "description": "操作结果消息"
            },
            "text": {
                "type": "string",
                "description": "输入的文本内容"
            },
            "error": {
                "type": "string",
                "description": "错误信息（仅在success为false时存在）"
            }
        },
        "required": ["success", "message"]
    }
}

# 添加CORS中间件以支持跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="screenshot",
            description="截取当前屏幕的截图，返回base64编码的图片数据",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        types.Tool(
            name="left_click",
            description="在指定坐标位置执行左键点击操作",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {
                        "type": "number",
                        "description": "点击位置的X坐标（必需）"
                    },
                    "y": {
                        "type": "number",
                        "description": "点击位置的Y坐标（必需）"
                    }
                },
                "required": ["x", "y"]
            }
        ),
        types.Tool(
            name="right_click",
            description="在指定坐标位置执行右键点击操作",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {
                        "type": "number",
                        "description": "点击位置的X坐标（必需）"
                    },
                    "y": {
                        "type": "number",
                        "description": "点击位置的Y坐标（必需）"
                    }
                },
                "required": ["x", "y"]
            }
        ),
        types.Tool(
            name="double_click",
            description="在指定坐标位置执行双击操作",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {
                        "type": "number",
                        "description": "双击位置的X坐标（必需）"
                    },
                    "y": {
                        "type": "number",
                        "description": "双击位置的Y坐标（必需）"
                    }
                },
                "required": ["x", "y"]
            }
        ),
        types.Tool(
            name="input_text",
            description="通过剪贴板方式输入文本（先设置剪贴板，再Ctrl+A全选，最后Ctrl+V粘贴）",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要输入的文本内容（必需）"
                    }
                },
                "required": ["text"]
            }
        )
    ]

def set_clipboard_text(text: str) -> bool:
    """设置剪贴板文本内容"""
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        print(f"[set_clipboard_text] 错误: {str(e)}")
        try:
            win32clipboard.CloseClipboard()
        except:
            pass
        return False

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list:
    """
    处理工具调用
    
    参数:
        name: 工具名称
        arguments: 工具参数
    """
    # 显示字幕窗口
    subtitle_window.show()
    
    try:
        if should_force_stop():
            return build_force_stop_response()
        
        # 工具名称映射到中文描述
        tool_descriptions = {
            "screenshot": "截图",
            "left_click": "左键点击",
            "right_click": "右键点击",
            "double_click": "双击",
            "input_text": "输入文本"
        }
        tool_desc = tool_descriptions.get(name, name)
        
        # 执行前显示即将执行，并等待
        subtitle_window.set_pending_operation(tool_desc)
        if PRE_EXECUTE_DELAY_SEC > 0:
            if not wait_with_force_stop(PRE_EXECUTE_DELAY_SEC):
                return build_force_stop_response()
        
        # 设置当前正在进行的操作
        subtitle_window.set_current_operation(tool_desc)
        
        if should_force_stop():
            return build_force_stop_response()
        
        if name == "screenshot":
            # 截屏操作
            screenshot = pyautogui.screenshot()
            # 转换为base64
            buffered = io.BytesIO()
            screenshot.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            # 设置已完成的操作
            subtitle_window.set_completed_operation(tool_desc)
            
            return [{
                "type": "object",
                "object": {
                    "success": True,
                    "message": "截图成功",
                    "image_base64": img_base64
                }
            }]
        
        elif name == "left_click":
            # 左键点击
            x = arguments.get("x")
            y = arguments.get("y")
            
            if x is None or y is None:
                subtitle_window.set_completed_operation(f"{tool_desc} (失败)")
                return [{
                    "type": "object",
                    "object": {
                        "success": False,
                        "message": "缺少必需的坐标参数",
                        "error": "x和y坐标不能为空"
                    }
                }]
            
            if should_force_stop():
                return build_force_stop_response()
            
            pyautogui.click(x, y)
            subtitle_window.set_completed_operation(tool_desc)
            
            return [{
                "type": "object",
                "object": {
                    "success": True,
                    "message": f"左键点击成功，坐标: ({x}, {y})",
                    "x": x,
                    "y": y
                }
            }]
        
        elif name == "right_click":
            # 右键点击
            x = arguments.get("x")
            y = arguments.get("y")
            
            if x is None or y is None:
                subtitle_window.set_completed_operation(f"{tool_desc} (失败)")
                return [{
                    "type": "object",
                    "object": {
                        "success": False,
                        "message": "缺少必需的坐标参数",
                        "error": "x和y坐标不能为空"
                    }
                }]
            
            if should_force_stop():
                return build_force_stop_response()
            
            pyautogui.rightClick(x, y)
            subtitle_window.set_completed_operation(tool_desc)
            
            return [{
                "type": "object",
                "object": {
                    "success": True,
                    "message": f"右键点击成功，坐标: ({x}, {y})",
                    "x": x,
                    "y": y
                }
            }]
        
        elif name == "double_click":
            # 双击
            x = arguments.get("x")
            y = arguments.get("y")
            
            if x is None or y is None:
                subtitle_window.set_completed_operation(f"{tool_desc} (失败)")
                return [{
                    "type": "object",
                    "object": {
                        "success": False,
                        "message": "缺少必需的坐标参数",
                        "error": "x和y坐标不能为空"
                    }
                }]
            
            if should_force_stop():
                return build_force_stop_response()
            
            pyautogui.doubleClick(x, y)
            subtitle_window.set_completed_operation(tool_desc)
            
            return [{
                "type": "object",
                "object": {
                    "success": True,
                    "message": f"双击成功，坐标: ({x}, {y})",
                    "x": x,
                    "y": y
                }
            }]
        
        elif name == "input_text":
            # 填写文本（通过剪贴板方式）
            text = arguments.get("text")
            
            if text is None:
                subtitle_window.set_completed_operation(f"{tool_desc} (失败)")
                return [{
                    "type": "object",
                    "object": {
                        "success": False,
                        "message": "缺少必需的文本参数",
                        "error": "text参数不能为空"
                    }
                }]
            
            if should_force_stop():
                return build_force_stop_response()
            
            # 设置剪贴板
            if not set_clipboard_text(text):
                subtitle_window.set_completed_operation(f"{tool_desc} (失败)")
                return [{
                    "type": "object",
                    "object": {
                        "success": False,
                        "message": "设置剪贴板失败",
                        "error": "无法设置剪贴板内容"
                    }
                }]
            
            # 等待剪贴板设置完成
            time.sleep(0.1)
            
            # Ctrl+A 全选
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.1)
            
            # Ctrl+V 粘贴
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
            
            subtitle_window.set_completed_operation(tool_desc)
            
            return [{
                "type": "object",
                "object": {
                    "success": True,
                    "message": f"文本输入成功: {text[:50]}{'...' if len(text) > 50 else ''}",
                    "text": text
                }
            }]
        
        else:
            subtitle_window.set_completed_operation(f"未知工具: {name}")
            return [{
                "type": "text",
                "text": f"未知的工具名称: {name}",
                "success": False
            }]
    
    except Exception as e:
        error_msg = str(e)
        print(f"[call_tool错误] {name}: {error_msg}")
        import traceback
        print(f"[call_tool错误] 错误详情:\n{traceback.format_exc()}")
        sys.stdout.flush()
        
        # 设置错误状态
        tool_descriptions = {
            "screenshot": "截图",
            "left_click": "左键点击",
            "right_click": "右键点击",
            "double_click": "双击",
            "input_text": "输入文本"
        }
        tool_desc = tool_descriptions.get(name, name)
        subtitle_window.set_completed_operation(f"{tool_desc} (错误)")
        
        return [{
            "type": "object",
            "object": {
                "success": False,
                "message": f"执行工具 {name} 时发生错误",
                "error": error_msg
            }
        }]
    finally:
        # call_tool完全结束后，关闭字幕窗口
        subtitle_window.hide_with_delay(SUBTITLE_HIDE_DELAY_SEC)

# 存储会话状态
sessions = {}

def ensure_json_serializable(obj):
    """确保对象可以JSON序列化"""
    if isinstance(obj, dict):
        return {k: ensure_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [ensure_json_serializable(item) for item in obj]
    elif isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    else:
        return str(obj)

async def handle_mcp_request(request_data: dict, session_id: str = None):
    """处理MCP请求
    
    参数:
        request_data: MCP请求数据
        session_id: 会话ID
    """
    if session_id is None:
        session_id = request_data.get("session_id", "default")
    
    if session_id not in sessions:
        sessions[session_id] = {
            "initialized": False,
            "request_id": 0
        }
    
    session = sessions[session_id]
    method = request_data.get("method")
    params = request_data.get("params", {})
    request_id = request_data.get("id")
    is_notification = request_id is None  # 通知没有id字段
    
    if not is_notification:
        session["request_id"] = max(session["request_id"], request_id if request_id else 0)
    
    # 处理初始化请求
    if method == "initialize":
        session["initialized"] = True
        
        # 回显客户端的 protocolVersion
        client_pv = params.get("protocolVersion") or "2024-11-05"

        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": client_pv,
                "capabilities": {
                    "tools": {"listChanged": True},
                    "logging": {}
                },
                "serverInfo": {
                    "name": "omni-windows-mcp-server",
                    "version": "1.0.0"
                }
            }
        }
        return ensure_json_serializable(response)
    
    # 处理初始化通知（通知不需要响应）
    if method == "notifications/initialized":
        return None
    
    # 处理工具列表请求
    if method == "tools/list":
        tools = await list_tools()
        # 将工具对象转换为字典格式
        tools_list = []
        for tool in tools:
            if hasattr(tool, "model_dump"):
                tool_dict = tool.model_dump()
            elif hasattr(tool, "dict"):
                tool_dict = tool.dict()
            elif isinstance(tool, dict):
                tool_dict = tool
            else:
                # 手动转换Tool对象
                tool_dict = {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema
                }
            
            # 手动添加 outputSchema（如果工具定义中有）
            tool_name = tool_dict.get("name") if isinstance(tool_dict, dict) else (tool.name if hasattr(tool, "name") else None)
            if tool_name and tool_name in TOOL_OUTPUT_SCHEMAS:
                # 使用深拷贝确保不会修改原始字典
                tool_dict["outputSchema"] = copy.deepcopy(TOOL_OUTPUT_SCHEMAS[tool_name])
            
            # 清除 title:null，避免 Cursor 某些严格校验分支
            if tool_dict.get("title") is None:
                tool_dict.pop("title", None)
            
            tools_list.append(tool_dict)
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": tools_list
            }
        }
        return ensure_json_serializable(response)

    # 处理资源列表请求
    if method == "resources/list":
        # 返回空资源列表（此服务器不提供资源）
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "resources": []
            }
        }
        return ensure_json_serializable(response)

    # 处理工具调用请求
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        print(f"\n>>> 执行工具调用: {tool_name}")
        if arguments:
            args_str = json.dumps(arguments, ensure_ascii=False, indent=2)
            if len(args_str) > 500:
                args_str = args_str[:500] + "\n... (已截断)"
            print(f"工具参数:\n{args_str}")
        sys.stdout.flush()
        
        # 调用工具
        result = await call_tool(tool_name, arguments)
        
        # 检查工具是否定义了 outputSchema
        tool_has_output_schema = tool_name in TOOL_OUTPUT_SCHEMAS
        
        # 将结果转换为MCP标准格式
        content = []
        structured_content = None
        
        for item in result:
            if isinstance(item, dict):
                item_type = item.get("type", "text")
                if item_type == "object":
                    # 结构化对象输出 - 提取结构化数据
                    structured_content = item.get("object", {})
                    # 同时提供文本格式的消息
                    message = structured_content.get("message", "")
                    if message:
                        content.append({
                            "type": "text",
                            "text": message
                        })
                else:
                    # 文本输出
                    content_item = {
                        "type": item_type,
                        "text": item.get("text", str(item))
                    }
                    content.append(content_item)
            else:
                # 非字典类型转换为标准格式
                content.append({
                    "type": "text",
                    "text": str(item)
                })
        
        # 根据MCP协议标准，响应格式应该包含content和structuredContent字段
        response_result = {
            "content": content
        }
        # 如果工具定义了 outputSchema，必须返回 structuredContent 字段
        if tool_has_output_schema:
            if structured_content is None:
                # 如果仍然没有结构化内容，创建一个默认的结构化响应
                structured_content = {
                    "success": False,
                    "message": "工具执行完成，但未返回结构化数据"
                }
            response_result["structuredContent"] = structured_content
        elif structured_content is not None:
            # 即使工具没有定义 outputSchema，如果有结构化内容也返回
            response_result["structuredContent"] = structured_content
        
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": response_result
        }
        return ensure_json_serializable(response)
    
    # 未知方法
    # 如果是通知，返回None；如果是请求，返回错误
    if is_notification:
        return None
    
    response = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32601,
            "message": f"未知方法: {method}"
        }
    }
    return ensure_json_serializable(response)

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """MCP HTTP端点"""
    request_id = None
    try:
        # 记录原始HTTP请求信息
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'='*80}")
        print(f"[{timestamp}] 收到HTTP请求")
        print(f"URL: {request.url}")
        print(f"方法: {request.method}")
        print(f"客户端: {request.client.host if request.client else '未知'}:{request.client.port if request.client else '未知'}")
        
        data = await request.json()
        request_id = data.get("id")
        
        # 记录原始请求体
        data_str = json.dumps(data, ensure_ascii=False, indent=2)
        if len(data_str) > 2000:
            data_str = data_str[:2000] + "\n... (已截断)"
        print(f"请求体:\n{data_str}")
        print(f"{'='*80}")
        sys.stdout.flush()
        
        # 从查询参数或请求头中获取session_id（如果提供）
        session_id = request.query_params.get("session_id") or request.headers.get("X-Session-ID")
        
        # 检查是否是标准MCP JSON-RPC格式
        is_standard_mcp = "jsonrpc" in data and "method" in data
        
        # 如果不是标准MCP格式，尝试检测是否是LLM工具调用格式
        if not is_standard_mcp:
            tool_name = None
            tool_arguments = None
            
            # 格式1: {"tool_name": "...", "arguments": {...}}
            if "tool_name" in data and "arguments" in data:
                tool_name = data.get("tool_name")
                tool_arguments = data.get("arguments", {})
            
            # 格式2: {"name": "...", "arguments": {...}} 且没有method字段
            elif "name" in data and "arguments" in data and "method" not in data:
                tool_name = data.get("name")
                tool_arguments = data.get("arguments", {})
            
            # 格式3: {"type": "tool_call", "name": "...", "arguments": {...}}
            elif data.get("type") == "tool_call" and "name" in data:
                tool_name = data.get("name")
                tool_arguments = data.get("arguments", {})
            
            # 如果检测到LLM工具调用格式，转换为标准MCP JSON-RPC格式
            if tool_name is not None:
                # 生成请求ID（如果没有提供）
                if request_id is None:
                    request_id = int(time.time() * 1000)
                
                # 转换为标准MCP JSON-RPC格式
                data = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": tool_arguments if tool_arguments else {}
                    }
                }
                print(f"\n[桥接层] 检测到LLM工具调用格式，已转换为MCP JSON-RPC格式")
                print(f"工具名称: {tool_name}")
                if tool_arguments:
                    args_str = json.dumps(tool_arguments, ensure_ascii=False, indent=2)
                    if len(args_str) > 500:
                        args_str = args_str[:500] + "\n... (已截断)"
                    print(f"工具参数:\n{args_str}")
                sys.stdout.flush()
            else:
                # 如果既不是标准MCP格式，也不是LLM工具调用格式，返回错误
                error_msg = "无法识别的请求格式。请使用标准MCP JSON-RPC格式或LLM工具调用格式。"
                print(f"\n[桥接层错误] {error_msg}")
                print(f"收到的数据: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}")
                sys.stdout.flush()
                return JSONResponse(
                    status_code=400,
                    content={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32600,
                            "message": error_msg
                        }
                    }
                )
        
        # 处理MCP请求
        response = await handle_mcp_request(data, session_id=session_id)
        # 如果响应为None（通知类型），返回200 OK with empty JSON body
        if response is None:
            return JSONResponse(content={}, status_code=200)
        
        # 响应已经通过ensure_json_serializable清理，应该可以安全序列化
        return JSONResponse(content=response)
    except json.JSONDecodeError as e:
        error_msg = f"JSON解析错误: {str(e)}"
        print(f"\n[错误] {error_msg}")
        sys.stdout.flush()
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32700,
                    "message": error_msg
                }
            }
        )
    except Exception as e:
        error_msg = f"内部错误: {str(e)}"
        print(f"\n[错误] {error_msg}")
        import traceback
        print(f"错误详情:\n{traceback.format_exc()}")
        sys.stdout.flush()
        return JSONResponse(
            status_code=500,
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": error_msg
                }
            }
        )

@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "ok", "service": "omni-windows-mcp-server"}

async def main():
    """启动HTTP服务器"""
    final_port = PORT
    final_host = HOST

    start_hotkey_listener()

    config = uvicorn.Config(
        app,
        host=final_host,
        port=final_port,
        log_level="info"
    )
    server_instance = uvicorn.Server(config)
    
    print("="*80)
    print("启动Omni Windows MCP服务器 (网络传输模式)")
    print("="*80)
    print(f"监听地址: {final_host}:{final_port}")
    print(f"HTTP端点: http://localhost:{final_port}/mcp")
    print(f"健康检查: http://localhost:{final_port}/health")
    print(f"\n提示: 可在文件顶部修改 PORT 和 HOST 全局变量来配置默认端口和主机")
    print("\n服务器已启动，等待请求...")
    print("="*80)
    sys.stdout.flush()
    
    # 运行服务器
    await server_instance.serve()

if __name__ == "__main__":    
    asyncio.run(main())
